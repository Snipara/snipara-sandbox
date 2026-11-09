"""Optional installed coordinator bridge; a launch is never retried as a raw run."""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from rlm.validation.managed_toolchain import ManagedToolchainError, selected_toolchain
from rlm.validation.models import SCHEMA, ValidationRequest
from rlm.validation.paths import secure_path
from rlm.validation.runner import CONTROL_ENV, canonical, redact
from rlm.validation.state import StateDirectory

MINIMUM_ORCHESTRATOR = (1, 15, 0)


def _execute(
    argv: list[str],
    *,
    root: Path,
    timeout: int,
    workspace_fd: int,
    cancel_event: threading.Event | None,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        argv,
        cwd=root,
        env=CONTROL_ENV if environment is None else environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        pass_fds=(workspace_fd,),
    )
    output, errors = bytearray(), bytearray()
    deadline = time.monotonic() + timeout
    try:
        assert process.stdout is not None and process.stderr is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, output)
            selector.register(process.stderr, selectors.EVENT_READ, errors)
            while selector.get_map() or process.poll() is None:
                if (
                    cancel_event is not None and cancel_event.is_set()
                ) or time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Coordinator interrupted; inspect original evidence before retrying."
                    )
                for key, _ in selector.select(0.05):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    key.data.extend(chunk)
                    if len(output) + len(errors) > 1024 * 1024:
                        raise ValueError("Coordinator output exceeds bounded response budget")
        return subprocess.CompletedProcess(
            argv, process.wait(timeout=1), output.decode(), errors.decode()
        )
    finally:
        # Own the whole process group; no retry can overlap an uncertain launch.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def coordinated_run(
    request: ValidationRequest,
    store: Path,
    *,
    root: Path,
    scope: str,
    no_reuse: bool = False,
    workspace_fd: int,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    launched = False
    phase = "coordinator_contract"
    try:
        if not isinstance(scope, str) or not scope.strip() or len(scope) > 256:
            raise ValueError(
                "Coordinated validation requires a bounded explicit workflow/lot scope"
            )
        if type(no_reuse) is not bool:
            raise ValueError("no_reuse must be a boolean")
        root = secure_path(root)
        workspace = secure_path(Path(request.workspace))
        store = secure_path(store)
        if not workspace.is_relative_to(root) or not store.is_relative_to(root):
            raise ValueError("Coordinated workspace and store must remain in the operator root")
        if not stat.S_ISDIR(os.fstat(workspace_fd).st_mode):
            raise ValueError("Coordinated validation requires a pinned workspace directory")
        managed = selected_toolchain()
        environment = managed.environment(CONTROL_ENV) if managed else None
        executable = (
            str(managed.components["orchestrator"].command)
            if managed
            else shutil.which("snipara-orchestrator")
        )
        if not executable:
            return {
                "status": "coordinator_unavailable",
                "reason_code": "coordinator_missing",
                "minimum_orchestrator_version": "1.15.0",
                "execution_authorized_by_gate": False,
                "next_action": "configure_optional_orchestrator_or_explicitly_choose_raw_execution",
            }
        phase = "coordinator_version"
        version = _execute(
            [executable, "--version"],
            root=root,
            timeout=10,
            workspace_fd=workspace_fd,
            cancel_event=cancel_event,
            environment=environment,
        )
        match = re.fullmatch(r"snipara-orchestrator (\d+)\.(\d+)\.(\d+)\s*", version.stdout)
        if (
            version.returncode
            or not match
            or tuple(map(int, match.groups())) < MINIMUM_ORCHESTRATOR
            or (
                managed is not None
                and ".".join(match.groups()) != managed.components["orchestrator"].version
            )
        ):
            return {
                "status": "coordinator_incompatible",
                "reason_code": "coordinator_version_unsupported",
                "minimum_orchestrator_version": "1.15.0",
                "execution_authorized_by_gate": False,
                "next_action": "configure_optional_orchestrator_1_15_or_newer",
            }
        sibling = Path(sys.executable).parent / "snipara-sandbox"
        sandbox = (
            str(managed.components["sandbox"].command)
            if managed
            else (
                str(sibling)
                if sibling.is_file() and os.access(sibling, os.X_OK)
                else shutil.which("snipara-sandbox")
            )
        )
        if not sandbox:
            raise ValueError("Current Sandbox CLI entrypoint is unavailable")
        phase = "coordinator_request"
        raw = canonical(request.identity_payload())
        digest = hashlib.sha256(raw).hexdigest()
        copies = secure_path(root / ".snipara" / "coordinated-requests")
        with StateDirectory.open(copies, create=True) as directory:
            info = os.fstat(directory.fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("Coordinated request directory must be private and owned")
            filename = digest + ".json"
            if not directory.exists(filename):
                directory.write(filename, raw)
            info = os.stat(filename, dir_fd=directory.fd, follow_symlinks=False)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("Coordinated request copy must be private and owned")
            if directory.read(filename, 65536) != raw:
                raise ValueError("Private coordinated request copy does not match reviewed request")
        argv = [
            executable,
            "validation",
            "run",
            "--request",
            str(copies / filename),
            "--scope",
            scope,
            "--store",
            str(store),
            "--coordination-root",
            str(root),
            "--sandbox-command",
            sandbox,
            "--workspace-fd",
            str(workspace_fd),
        ]
        if no_reuse:
            argv.append("--no-reuse")
        phase = "coordinator_execution"
        launched = True
        result = _execute(
            argv,
            root=root,
            timeout=request.timeout_seconds + 180,
            workspace_fd=workspace_fd,
            cancel_event=cancel_event,
            environment=environment,
        )
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError("Coordinator response must be an object")
        if value.get("status") not in {
            "passed",
            "failed",
            "timeout",
            "cancelled",
            "environment_incompatible",
            "artifact_error",
            "collection_error",
            "cleanup_failed",
            "error",
            "interrupted",
            "validation_reuse_needs_review",
            "validation_in_progress",
            "needs_user_choice",
            "user_choice_recorded",
        }:
            raise ValueError("Coordinator response has an unsupported terminal status")
        reuse = value.get("work_reuse")
        if not isinstance(reuse, dict) or reuse.get("state") not in {
            "executed",
            "reused",
            "needs_review",
            "in_progress",
        }:
            raise ValueError("Coordinator response lacks scoped execution evidence")
        if value.get("status") == "passed" and (
            result.returncode != 0
            or value.get("schema_version") != SCHEMA
            or value.get("backend") != "docker"
            or value.get("cleanup_complete") is not True
            or type(value.get("exit_code")) is not int
            or value["exit_code"] != 0
            or reuse.get("state") not in {"executed", "reused"}
            or not re.fullmatch("[a-f0-9]{32}", str(value.get("run_id", "")))
        ):
            raise ValueError("Coordinator response contradicts successful validation")
        return value
    except (OSError, ValueError, subprocess.SubprocessError, UnicodeError) as error:
        return {
            "status": "validation_reuse_needs_review"
            if launched
            else "validation_contract_invalid",
            "execution_authorized_by_gate": False,
            "reason_code": (
                error.code
                if isinstance(error, ManagedToolchainError)
                else "coordinator_execution_uncertain"
                if launched
                else "coordinator_contract_invalid"
            ),
            "stage": phase,
            "reason": redact(str(error))[:2000],
            "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
            "work_reuse": {"state": "needs_review"},
            "next_action": "inspect_original_coordinator_and_sandbox_evidence"
            if launched
            else "review_coordinator_configuration",
        }
