"""Native scoped validation: per-work exclusion and freshly verified receipt reuse.

Sandbox 3.2 runs `validation run --scope` itself instead of delegating to an
installed `snipara-orchestrator validation run --scope` (ported from
snipara-orchestrator 2.1.0 `validation_reuse.py`).

Reuse state is trusted single-user macOS/Linux state under the repository's
private `.snipara/validation-reuse` directory, never a distributed cache or a
proof authority: an indexed receipt only shortlists a candidate, and only a
fresh source/image/artifact verification proves it. A pending marker survives an
interrupted owner and requires human review; a launch is never retried.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import threading
import time
from pathlib import Path
from typing import Any

from rlm import __version__
from rlm.validation.environment import gate
from rlm.validation.identity import scope_identity
from rlm.validation.models import SCHEMA, ValidationRequest
from rlm.validation.paths import canonical_system_path, secure_path
from rlm.validation.runner import RUN_ID, canonical, redact, request_hash, run, verify
from rlm.validation.snapshot import pin_directory
from rlm.validation.state import StateDirectory

IMMUTABLE_IMAGE = re.compile(r"(?:[^\s]+@)?sha256:[a-f0-9]{64}")
MAX_RECORD = 1024 * 1024
MAX_AGE_SECONDS = 86400
REUSE_DIRECTORY = (".snipara", "validation-reuse")
# A change to how work is executed or verified invalidates every indexed receipt.
IMPLEMENTATION = (
    "coordinator.py",
    "environment.py",
    "identity.py",
    "models.py",
    "runner.py",
    "snapshot.py",
)


class _Blocked(Exception):
    def __init__(self, reason: str, reason_code: str, *, busy: bool = False):
        super().__init__(reason)
        self.reason_code = reason_code
        self.busy = busy


def blocked(
    reason: str,
    *,
    key: str | None = None,
    busy: bool = False,
    reason_code: str = "scoped_validation_needs_review",
    stage: str = "scoped_contract",
) -> dict[str, Any]:
    """Nonexecution outcome; it never carries a run identity."""
    return {
        "status": "validation_in_progress" if busy else "validation_reuse_needs_review",
        "execution_authorized_by_gate": False,
        "reason_code": reason_code,
        "stage": stage,
        "work_reuse": {
            "state": "in_progress" if busy else "needs_review",
            "key": key,
            "reason": redact(reason)[:2000],
        },
    }


def _successful(receipt: Any) -> bool:
    return (
        isinstance(receipt, dict)
        and receipt.get("schema_version") == SCHEMA
        and bool(RUN_ID.fullmatch(str(receipt.get("run_id", ""))))
        and receipt.get("status") == "passed"
        and receipt.get("backend") == "docker"
        and receipt.get("cleanup_complete") is True
        and type(receipt.get("exit_code")) is int
        and receipt["exit_code"] == 0
    )


def _repository(workspace: Path, root: Path | None) -> Path:
    if root is not None:
        return root
    return next((p for p in (workspace, *workspace.parents) if (p / ".git").exists()), workspace)


def _open_reuse_directory(repository: Path) -> StateDirectory:
    path = secure_path(repository.joinpath(*REUSE_DIRECTORY))
    directory = StateDirectory.open(path, create=True)
    try:
        parent = os.lstat(path.parent)
        info = os.fstat(directory.fd)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) & 0o022
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ValueError("Reuse directory must be private, owned and contain no links")
        return directory
    except BaseException:
        directory.close()
        raise


def _private(directory: StateDirectory, name: str) -> None:
    info = os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValueError("Reuse state must be private and owned by the current user")


def _record(directory: StateDirectory, name: str) -> dict[str, Any] | None:
    if not directory.exists(name):
        return None
    _private(directory, name)
    value = json.loads(directory.read(name, MAX_RECORD))
    if not isinstance(value, dict):
        raise ValueError("Invalid reuse record")
    return value


def _write(directory: StateDirectory, name: str, value: dict[str, Any]) -> None:
    data = canonical(value)
    if len(data) > MAX_RECORD:
        raise ValueError("Reuse record exceeds its bounded size")
    directory.write(name, data)
    os.fsync(directory.fd)


def _remove(directory: StateDirectory, name: str) -> None:
    try:
        os.unlink(StateDirectory.name(name), dir_fd=directory.fd)
    except FileNotFoundError:
        return
    os.fsync(directory.fd)


def _implementation() -> dict[str, str]:
    here = Path(__file__).parent
    return {name: hashlib.sha256((here / name).read_bytes()).hexdigest() for name in IMPLEMENTATION}


def reuse_key(
    request: ValidationRequest, scope: str, *, repository: Path, workspace: Path, store: Path
) -> str:
    identity = {
        "schema_version": "snipara.sandbox.validation.reuse.v1",
        "request_sha256": request_hash(request),
        "scope": scope,
        "scope_sha256": scope_identity(request, scope),
        "repository": str(repository),
        "workspace": str(workspace),
        "store": str(store),
        "image": request.image,
        "sandbox_version": __version__,
        "implementation": _implementation(),
    }
    return hashlib.sha256(canonical(identity)).hexdigest()


def _verified(
    request: ValidationRequest,
    store: Path,
    receipt: dict[str, Any],
    workspace_fd: int,
    cancel_event: threading.Event | None,
) -> bool:
    value = verify(
        request, store, receipt["run_id"], workspace_fd=workspace_fd, cancel_event=cancel_event
    )
    return (
        isinstance(value, dict)
        and value.get("schema_version") == SCHEMA
        and value.get("run_id") == receipt["run_id"]
        and value.get("valid") is True
        and value.get("reasons") == []
        and value.get("receipt") == receipt
        and _successful(value["receipt"])
    )


def _reusable(candidate: dict[str, Any], key: str, now: float) -> dict[str, Any] | None:
    indexed: Any = candidate.get("indexed_at")
    receipt: Any = candidate.get("receipt")
    if (
        candidate.get("key") == key
        and type(indexed) in (int, float)
        and math.isfinite(indexed)
        and 0 <= now - indexed <= MAX_AGE_SECONDS
        and _successful(receipt)
    ):
        return dict(receipt)
    return None


def _under_lock(
    request: ValidationRequest,
    store: Path,
    scope: str,
    *,
    no_reuse: bool,
    key: str,
    directory: StateDirectory,
    now: float,
    workspace_fd: int,
    cancel_event: threading.Event | None,
    progress: dict[str, str],
) -> dict[str, Any]:
    pending, index = f"{key}.pending.json", f"{key}.json"
    if _record(directory, pending) is not None:
        raise _Blocked(
            "Previous execution has uncertain cleanup; review its original Sandbox evidence, "
            f"then remove .snipara/validation-reuse/{pending}.",
            "previous_execution_uncertain",
        )
    progress["stage"] = "scoped_gate"
    readiness = gate(request, scope, workspace_fd=workspace_fd)
    ready = readiness.get("readiness")
    if (
        readiness.get("status") != "sandbox_ready"
        or not isinstance(ready, dict)
        or ready.get("ready") is not True
        or ready.get("backend") != "docker"
    ):
        # Explicit operator choices stay visible; nothing executes without a ready gate.
        return {**readiness, "work_reuse": {"state": "needs_review", "key": key}}
    progress["stage"] = "scoped_reuse"
    candidate = None if no_reuse else _record(directory, index)
    receipt = _reusable(candidate, key, now) if candidate else None
    if receipt is not None:
        try:
            if _verified(request, store, receipt, workspace_fd, cancel_event):
                return {**receipt, "work_reuse": {"state": "reused", "key": key, "checked_at": now}}
        except (OSError, ValueError, TypeError, KeyError):
            pass  # Unverifiable evidence is never reuse; execute fresh instead.
    _remove(directory, index)  # A rejected candidate never survives as a shortlist hit.
    _write(directory, pending, {"key": key, "state": "running", "started_at": now})
    progress["stage"] = "scoped_execution"
    receipt = run(request, store, cancel_event=cancel_event, workspace_fd=workspace_fd)
    metadata: dict[str, Any] = {"state": "executed", "key": key, "checked_at": now}
    if receipt.get("cleanup_complete") is not True:
        # The pending marker is intentionally kept: this work needs human review.
        return {**receipt, "work_reuse": {**metadata, "state": "needs_review"}}
    _remove(directory, pending)  # The terminal receipt has established cleanup.
    if _successful(receipt):
        progress["stage"] = "scoped_index"
        try:
            if _verified(request, store, receipt, workspace_fd, cancel_event):
                _write(directory, index, {"key": key, "indexed_at": now, "receipt": receipt})
            else:
                metadata["state"] = "needs_review"
        except (OSError, ValueError, TypeError, KeyError):
            metadata.update(
                state="needs_review",
                reason="Execution cleanup is confirmed; receipt verification or index persistence failed.",
            )
    return {**receipt, "work_reuse": metadata}


def coordinated_run(
    request: ValidationRequest,
    store: Path,
    *,
    scope: str,
    root: Path | None = None,
    no_reuse: bool = False,
    workspace_fd: int | None = None,
    cancel_event: threading.Event | None = None,
    clock: Any = time.time,
) -> dict[str, Any]:
    """Hold one per-work OS lock from shortlisting through verification and execution."""
    key = None
    owned_fd = None
    directory = None
    lock = None
    progress = {"stage": "scoped_contract"}
    try:
        if os.name != "posix":
            raise _Blocked(
                "Scoped validation currently qualifies macOS/Linux only.", "platform_unsupported"
            )
        if not isinstance(scope, str) or not scope.strip() or len(scope) > 256:
            raise ValueError("Scoped validation requires a bounded explicit workflow/lot scope")
        if type(no_reuse) is not bool:
            raise ValueError("no_reuse must be a boolean")
        if not IMMUTABLE_IMAGE.fullmatch(request.image):
            raise ValueError("Scoped validation requires an immutable sha256 image")
        workspace = secure_path(canonical_system_path(Path(request.workspace)))
        store = secure_path(store)
        if root is not None:
            root = secure_path(root)
            if not root.is_dir():
                raise ValueError("Coordination root must be an existing directory")
            if not workspace.is_relative_to(root) or not store.is_relative_to(root):
                raise ValueError("Coordinated workspace and store must remain in the operator root")
        if workspace_fd is None:
            owned_fd = workspace_fd = pin_directory(workspace)
        elif not stat.S_ISDIR(os.fstat(workspace_fd).st_mode):
            raise ValueError("Coordinated validation requires a pinned workspace directory")
        repository = _repository(workspace, root)
        key = reuse_key(request, scope, repository=repository, workspace=workspace, store=store)
        directory = _open_reuse_directory(repository)
        import fcntl

        lock = os.open(
            f"{key}.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=directory.fd,
        )
        info = os.fstat(lock)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ValueError("Reuse lock must be a private owned regular file")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise _Blocked(
                "Identical scoped validation is already running.",
                "scoped_work_in_progress",
                busy=True,
            ) from None
        return _under_lock(
            request,
            store,
            scope,
            no_reuse=no_reuse,
            key=key,
            directory=directory,
            now=clock(),
            workspace_fd=workspace_fd,
            cancel_event=cancel_event,
            progress=progress,
        )
    except _Blocked as error:
        return blocked(
            str(error),
            key=key,
            busy=error.busy,
            reason_code=error.reason_code,
            stage=progress["stage"],
        )
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError) as error:
        # A pending marker, if execution began, is intentionally retained.
        launched = progress["stage"] in {"scoped_execution", "scoped_index"}
        return blocked(
            f"Scoped validation contract or evidence error ({type(error).__name__}): {error}",
            key=key,
            reason_code="scoped_execution_uncertain" if launched else "scoped_contract_invalid",
            stage=progress["stage"],
        )
    finally:
        if lock is not None:
            os.close(lock)
        if directory is not None:
            directory.close()
        if owned_fd is not None:
            os.close(owned_fd)
