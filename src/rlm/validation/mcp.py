"""MCP adapter confined to the operator-configured workspace; no host fallback."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

from mcp.types import CallToolResult, TextContent, Tool

from rlm.validation.coordinator import coordinated_run
from rlm.validation.models import ValidationRequest
from rlm.validation.paths import canonical_system_path, secure_path
from rlm.validation.profiles_catalog import inspect_request, validation_profiles
from rlm.validation.runner import cancel, diagnose, read_run, recover, run, verify
from rlm.validation.snapshot import excluded, pin_directory_bounded

NAMES = {
    "validation_profiles",
    "validation_inspect",
    "validation_diagnose",
    "validation_run",
    "validation_status",
    "validation_verify",
    "validation_cancel",
    "validation_recover",
}


def validation_tools() -> list[Tool]:
    result = []
    for name in sorted(NAMES):
        properties: dict[str, Any] = {}
        definitions: dict[str, Any] = {}
        required = []
        if name in {
            "validation_diagnose",
            "validation_run",
            "validation_verify",
            "validation_inspect",
        }:
            request_schema = ValidationRequest.model_json_schema()
            # Local JSON pointers resolve from the complete tool document,
            # even when the request model is nested under properties.request.
            definitions = request_schema.pop("$defs", {})
            properties["request"] = request_schema
            required.append("request")
        if name in {
            "validation_status",
            "validation_verify",
            "validation_cancel",
            "validation_recover",
        }:
            properties["run_id"] = {"type": "string", "pattern": "^[a-f0-9]{32}$"}
            required.append("run_id")
        if name == "validation_run":
            properties["scope"] = {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
                "description": "Recommended normal path: explicit workflow/lot scope selects optional installed Orchestrator coordination and verified reuse. Omission retains legacy raw execution outside the coordination guarantee.",
            }
            properties["no_reuse"] = {
                "type": "boolean",
                "default": False,
                "description": "Force fresh scoped execution, retaining exclusion; requires scope.",
            }
        result.append(
            Tool(
                name=name,
                description="Deterministic repository validation in owned Docker jobs; confined to configured workspace; no installation, inference or local fallback. "
                + (
                    "Scope is the recommended normal path. Omission retains legacy raw execution outside the coordination guarantee. "
                    if name == "validation_run"
                    else ""
                )
                + name,
                inputSchema={
                    **({"$defs": definitions} if definitions else {}),
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            )
        )
    return result


async def handle(name: str, arguments: dict[str, Any], root: Path) -> CallToolResult:
    workspace_fd = None
    try:
        if name == "validation_profiles":
            if arguments:
                raise ValueError("validation_profiles takes no arguments")
            value = validation_profiles()
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(value))], structuredContent=value
            )
        if name == "validation_inspect":
            if set(arguments) != {"request"}:
                raise ValueError("validation_inspect requires only request")
            value = inspect_request(ValidationRequest.model_validate(arguments["request"]))
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(value))], structuredContent=value
            )
        if name not in NAMES:
            raise ValueError("Unknown validation tool")
        allowed = (
            {"request", "scope", "no_reuse"}
            if name == "validation_run"
            else {"request", "run_id"}
            if name == "validation_verify"
            else {"request"}
            if name == "validation_diagnose"
            else {"run_id"}
        )
        if set(arguments) - allowed:
            raise ValueError("Unknown validation argument")
        scope = arguments.get("scope")
        if "scope" in arguments and (
            not isinstance(scope, str) or not scope.strip() or len(scope) > 256
        ):
            raise ValueError("scope must be an explicit bounded workflow/lot string")
        no_reuse = arguments.get("no_reuse", False)
        if type(no_reuse) is not bool or (no_reuse and scope is None):
            raise ValueError("no_reuse requires an explicit scope and a boolean value")
        root = root.absolute()
        store = secure_path(root / ".snipara/validation")
        if not store.resolve().is_relative_to(root):
            raise ValueError("MCP store escapes configured root")
        request = None
        if "request" in arguments:
            request = ValidationRequest.model_validate(arguments["request"])
            workspace = canonical_system_path(Path(request.workspace))
            if not workspace.is_relative_to(root):
                raise ValueError("workspace is outside operator-configured MCP root")
            if any(excluded(part) for part in workspace.relative_to(root).parts):
                raise ValueError("workspace traverses an excluded source directory")
            acquisition = asyncio.create_task(
                asyncio.to_thread(pin_directory_bounded, workspace, root=root)
            )
            try:
                workspace_fd = await asyncio.shield(acquisition)
            except asyncio.CancelledError:
                try:
                    late_fd = await asyncio.shield(acquisition)
                except Exception:
                    pass
                else:
                    os.close(late_fd)
                raise
            request = request.model_copy(update={"workspace": str(workspace)})

        async def with_workspace(function: Any, *args: Any, **kwargs: Any) -> Any:
            # Transfer an independent lease into the worker before scheduling it.
            held_fd = os.dup(workspace_fd) if workspace_fd is not None else None
            event = threading.Event()

            def execute() -> Any:
                try:
                    return function(*args, workspace_fd=held_fd, cancel_event=event, **kwargs)
                finally:
                    if held_fd is not None:
                        os.close(held_fd)

            worker = asyncio.create_task(asyncio.to_thread(execute))
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                event.set()
                with suppress(Exception):
                    await asyncio.shield(worker)
                raise

        if name == "validation_run":
            if request is None:
                raise ValueError("request required")
            value = (
                await with_workspace(
                    coordinated_run, request, store, root=root, scope=scope, no_reuse=no_reuse
                )
                if scope is not None
                else await with_workspace(run, request, store)
            )
        elif name == "validation_diagnose":
            if request is None:
                raise ValueError("request required")
            value = await with_workspace(diagnose, request)
        elif name == "validation_verify":
            if request is None:
                raise ValueError("request required")
            value = await with_workspace(verify, request, store, arguments["run_id"])
        elif name == "validation_recover":
            value = await asyncio.to_thread(recover, store, arguments["run_id"])
        elif name == "validation_cancel":
            value = await asyncio.to_thread(cancel, store, arguments["run_id"])
        else:
            value = await asyncio.to_thread(read_run, store, arguments["run_id"])
        if name == "validation_run":
            # Response-only sideband: persisted receipts remain exact verifier-owned evidence.
            value = {
                **value,
                "coordination_mode": "coordinated" if scope is not None else "legacy_raw",
            }
        error = (
            value.get("status")
            in {
                "error",
                "failed",
                "timeout",
                "cancelled",
                "environment_incompatible",
                "cleanup_failed",
                "artifact_error",
                "collection_error",
                "coordinator_unavailable",
                "coordinator_incompatible",
                "validation_contract_invalid",
                "validation_reuse_needs_review",
                "validation_in_progress",
                "needs_user_choice",
                "user_choice_recorded",
            }
            or value.get("valid") is False
            or value.get("ready") is False
        )
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(value))],
            structuredContent=value,
            isError=error,
        )
    except Exception as error:
        return CallToolResult(
            content=[TextContent(type="text", text=str(error)[:2000])], isError=True
        )

    finally:
        if workspace_fd is not None:
            os.close(workspace_fd)
