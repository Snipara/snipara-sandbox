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

from rlm.validation.models import ValidationRequest
from rlm.validation.paths import secure_path
from rlm.validation.runner import cancel, diagnose, read_run, recover, run, verify
from rlm.validation.snapshot import pin_directory

NAMES = {
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
        required = []
        if name in {"validation_diagnose", "validation_run", "validation_verify"}:
            properties["request"] = ValidationRequest.model_json_schema()
            required.append("request")
        if name in {
            "validation_status",
            "validation_verify",
            "validation_cancel",
            "validation_recover",
        }:
            properties["run_id"] = {"type": "string", "pattern": "^[a-f0-9]{32}$"}
            required.append("run_id")
        result.append(
            Tool(
                name=name,
                description="Deterministic repository validation in owned Docker jobs; confined to configured workspace; no installation, inference or local fallback. "
                + name,
                inputSchema={
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
        root = root.absolute()
        store = secure_path(root / ".snipara/validation")
        if not store.resolve().is_relative_to(root):
            raise ValueError("MCP store escapes configured root")
        request = None
        if "request" in arguments:
            request = ValidationRequest.model_validate(arguments["request"])
            workspace = Path(request.workspace).resolve(strict=True)
            if not workspace.is_relative_to(root):
                raise ValueError("workspace is outside operator-configured MCP root")
            workspace_fd = pin_directory(workspace, root=root)
            request = request.model_copy(update={"workspace": str(workspace)})

        async def with_workspace(function: Any, *args: Any) -> Any:
            # Transfer an independent lease into the worker before scheduling it.
            held_fd = os.dup(workspace_fd) if workspace_fd is not None else None
            event = threading.Event()

            def execute() -> Any:
                try:
                    return function(*args, workspace_fd=held_fd, cancel_event=event)
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
            value = await with_workspace(run, request, store)
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
