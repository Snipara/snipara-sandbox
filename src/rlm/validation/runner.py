"""Owned Docker validation jobs. No inference, shell interpolation or host fallback."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from rlm.validation.artifact_readers import NODE_READER, PYTHON_READER
from rlm.validation.models import SCHEMA, ValidationRequest
from rlm.validation.paths import secure_path
from rlm.validation.profiles import probe_script
from rlm.validation.snapshot import SnapshotCancelledError, capture_bounded

# Capture operator transport settings before any legacy project dotenv loading.
CONTROL_ENV = {
    key: value
    for key, value in os.environ.items()
    if key
    in {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_CONFIG",
    }
}

LABEL = "com.snipara.sandbox.validation"
MAX_LOG = 65536
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
RUN_ID = re.compile(r"^[a-f0-9]{32}$")


def docker_client() -> Any:
    """Honor Docker's selected context without loading project secrets."""
    import docker

    if CONTROL_ENV.get("DOCKER_HOST"):
        host = CONTROL_ENV["DOCKER_HOST"]
        if not host.startswith("unix://"):
            raise ValueError(
                "repository validation currently qualifies local Unix Docker sockets only"
            )
        return docker.DockerClient(base_url=host, timeout=10)
    result = subprocess.run(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
        env=CONTROL_ENV,
    )
    host = result.stdout.strip()
    if not host.startswith("unix://"):
        raise ValueError("repository validation currently qualifies local Unix Docker sockets only")
    return docker.DockerClient(base_url=host, timeout=10)


def redact(value: str) -> str:
    value = re.sub(
        r"(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]", value
    )
    return re.sub(r"(?i)(--(?:api-key|token|password|secret)\s+)[^\s]+", r"\1[REDACTED]", value)


def redact_argv(argv: list[str]) -> list[str]:
    display = []
    secret_next = False
    for value in argv:
        display.append("[REDACTED]" if secret_next else redact(value))
        secret_next = value.lower() in {"--token", "--api-key", "--password", "--secret"}
    return display


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def request_hash(request: ValidationRequest) -> str:
    return hashlib.sha256(canonical(request.model_dump())).hexdigest()


def _write(directory: Path, receipt: dict[str, Any]) -> None:
    target = directory / "receipt.json"
    temporary = directory / (".receipt-" + uuid.uuid4().hex)
    temporary.write_bytes(canonical(receipt))
    temporary.chmod(0o600)
    temporary.replace(target)


def read_run(store: Path, run_id: str) -> dict[str, Any]:
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run id")
    store = secure_path(store)
    target = secure_path(store / run_id / "receipt.json")
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError("symlink receipt refused")
    if target.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("receipt exceeds byte budget")
    value = json.loads(target.read_text())
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != SCHEMA
        or value.get("run_id") != run_id
    ):
        raise ValueError("receipt schema or run identity mismatch")
    return value


def diagnose(
    request: ValidationRequest,
    *,
    client: Any = None,
    workspace_fd: int | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Check daemon, image and project, never install or run local commands."""
    report: dict[str, Any] = {
        "schema_version": SCHEMA,
        "status": "unavailable",
        "backend": "docker",
        "ready": False,
        "profile": request.profile,
    }
    owned = client is None
    try:
        client = client or docker_client()
        client.ping()
        image = client.images.get(request.image)
        capture_bounded(
            Path(request.workspace),
            deadline=time.monotonic() + min(request.timeout_seconds, 10),
            workspace_fd=workspace_fd,
            cancelled=lambda: cancel_event is not None and cancel_event.is_set(),
        )
        with tempfile.TemporaryDirectory(prefix="snipara-diagnose-") as folder:
            directory = Path(folder)
            project = directory / "project"
            project.mkdir()
            probe = request.model_copy(
                update={
                    "workspace": str(project),
                    "argv": ["true"],
                    "cwd": ".",
                    "artifacts": [],
                    "timeout_seconds": min(request.timeout_seconds, 10),
                }
            )
            checked = run(probe, directory / "runs", client=client, cancel_event=cancel_event)
            report.update(
                status="ready" if checked["status"] == "passed" else "incompatible",
                ready=checked["status"] == "passed",
                image_id=image.id,
                docker_version=client.version()["Version"],
                runtime_versions=checked.get("runtime_versions", ""),
                probe_status=checked["status"],
            )
            if not report["ready"]:
                report["next_action"] = "needs_user_choice"
    except Exception as error:
        report["reason"] = redact(str(error))[:2000]
        report["next_action"] = "needs_user_choice"
    finally:
        if owned and client is not None:
            client.close()
    return report


def _owned_container(client: Any, receipt: dict[str, Any]) -> Any:
    container = client.containers.get(receipt["container_id"])
    if container.labels.get(LABEL) != receipt["run_id"]:
        raise ValueError("container ownership mismatch")
    return container


def cancel(store: Path, run_id: str, *, client: Any = None) -> dict[str, Any]:
    receipt = read_run(store, run_id)
    if receipt["status"] != "running":
        return {"run_id": run_id, "cancel_requested": False, "status": receipt["status"]}
    owned = client is None
    try:
        client = client or docker_client()
        container = _owned_container(client, receipt)
        (store / run_id / "cancel-requested").touch(mode=0o600)
        container.kill()
        return {"run_id": run_id, "cancel_requested": True}
    finally:
        if owned and client is not None:
            client.close()


def _read_file(
    container: Any,
    root: str,
    relative: str,
    profile: str,
    client: Any,
    limit: int,
    *,
    truncate: bool = False,
) -> bytes:
    arguments = [root, relative, str(limit), "truncate" if truncate else "complete"]
    argv = (
        ["node", "-e", NODE_READER, *arguments]
        if profile == "node"
        else ["python", "-c", PYTHON_READER, *arguments]
    )
    execution = client.api.exec_create(container.id, argv, stdout=True, stderr=False)["Id"]
    stream = client.api.exec_start(execution, stream=True)
    data = bytearray()
    try:
        for chunk in stream:
            data.extend(chunk)
            if len(data) > limit + int(truncate):
                raise ValueError("file export exceeds byte budget")
    finally:
        with suppress(OSError):
            stream.close()
    state = client.api.exec_inspect(execution)
    if state.get("Running") or state.get("ExitCode") != 0:
        raise ValueError("requested file could not be safely exported: " + relative)
    return bytes(data)


def _collect(
    container: Any, directory: Path, paths: list[str], profile: str, client: Any
) -> list[dict[str, Any]]:
    artifacts = []
    total = 0
    for index, relative in enumerate(paths):
        data = _read_file(container, "/work", relative, profile, client, MAX_ARTIFACT_BYTES - total)
        total += len(data)
        name = f"artifact-{index}-0"
        (directory / name).write_bytes(data)
        artifacts.append(
            {
                "requested_path": relative,
                "name": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )
    return artifacts


def _execute(container: Any, client: Any, argv: list[str], deadline: float, cancelled: Any) -> int:
    execution = client.api.exec_create(container.id, argv)["Id"]
    client.api.exec_start(execution, detach=True)
    while True:
        if cancelled():
            raise SnapshotCancelledError("job cancelled")
        if time.monotonic() >= deadline:
            raise TimeoutError("job deadline exceeded")
        state = client.api.exec_inspect(execution)
        if not state["Running"]:
            return int(state["ExitCode"])
        time.sleep(0.05)


def run(
    request: ValidationRequest,
    store: Path,
    *,
    client: Any = None,
    cancel_event: threading.Event | None = None,
    workspace_fd: int | None = None,
) -> dict[str, Any]:
    """Run a snapshot once, with explicit lifecycle ownership and durable evidence."""
    store = secure_path(store)
    project = Path(request.workspace).resolve()
    resolved_store = store.resolve()
    if resolved_store == project or (
        resolved_store.is_relative_to(project)
        and resolved_store.relative_to(project).parts[0] != ".snipara"
    ):
        raise ValueError("store inside workspace must be under excluded .snipara directory")
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    if store.is_symlink():
        raise ValueError("symlink store refused")
    run_id = uuid.uuid4().hex
    directory = store / run_id
    directory.mkdir(mode=0o700)
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA,
        "run_id": run_id,
        "backend": "docker",
        "authority": "local_runtime_observed",
        "status": "preparing",
        "request_sha256": request_hash(request),
        "command_sha256": hashlib.sha256(canonical(request.argv)).hexdigest(),
        "argv": redact_argv(request.argv),
        "cwd": request.cwd,
        "artifacts": [],
        "cleanup_complete": False,
        "started_at": time.time(),
    }
    _write(directory, receipt)
    container = None
    owned = client is None
    started = time.monotonic()
    deadline = started + request.timeout_seconds
    monitor_stop = threading.Event()
    abort: dict[str, str] = {}

    def monitor() -> None:
        while not monitor_stop.wait(0.05):
            reason = (
                "cancelled"
                if (
                    (directory / "cancel-requested").exists()
                    or (cancel_event is not None and cancel_event.is_set())
                )
                else ("timeout" if time.monotonic() >= deadline else "")
            )
            if reason:
                abort["reason"] = reason
                if container is not None:
                    try:
                        container.kill()
                    except Exception:
                        pass
                    return

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    try:
        client = client or docker_client()
        client.ping()
        image = client.images.get(request.image)
        receipt["image_id"] = image.id
        if image.attrs.get("Config", {}).get("Volumes"):
            raise ValueError("image-declared volumes bypass validation disk limits")
        with tempfile.TemporaryDirectory(prefix="snipara-validation-" + run_id + "-") as temporary:
            snapshot = Path(temporary)
            snapshot.chmod(0o755)
            receipt["snapshot_path"] = str(snapshot.resolve())
            _write(directory, receipt)
            receipt["snapshot"] = capture_bounded(
                Path(request.workspace),
                snapshot,
                deadline=deadline,
                cancelled=lambda: (directory / "cancel-requested").exists()
                or (cancel_event is not None and cancel_event.is_set()),
                workspace_fd=workspace_fd,
            )
            if not (snapshot / request.cwd).is_dir():
                raise ValueError("cwd missing from snapshot")
            if abort:
                raise TimeoutError("validation budget expired during preparation")
            # Fixed bootstrap receives positional argv; user strings are never interpolated.
            bootstrap = 'cp -R /input/. /work/ && cd "$1" && shift && exec "$@" > /tmp/validation-output 2>&1'
            container = client.containers.create(
                image.id,
                command=["sleep", str(request.timeout_seconds + 15)],
                name="snipara-validation-" + run_id,
                labels={LABEL: run_id},
                volumes={str(snapshot): {"bind": "/input", "mode": "ro"}},
                tmpfs={
                    "/work": f"rw,exec,nosuid,nodev,size={request.disk_mb}m,mode=1777",
                    "/tmp": "rw,exec,nosuid,nodev,size=64m,mode=1777",
                },
                read_only=True,
                user="65534:65534",
                network_disabled=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit=f"{request.memory_mb}m",
                memswap_limit=f"{request.memory_mb}m",
                nano_cpus=int(request.cpus * 1_000_000_000),
                pids_limit=request.pids,
                environment={
                    "HOME": "/tmp",
                    "XDG_CACHE_HOME": "/tmp/cache",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTEST_ADDOPTS": "-o cache_dir=/tmp/pytest-cache",
                },
                log_config={"Type": "json-file", "Config": {"max-size": "1m", "max-file": "1"}},
                detach=True,
            )
            receipt.update(status="running", container_id=container.id)
            _write(directory, receipt)
            container.start()

            def cancelled() -> bool:
                return (directory / "cancel-requested").exists() or (
                    cancel_event is not None and cancel_event.is_set()
                )

            probe_code = _execute(
                container,
                client,
                ["/bin/sh", "-c", "(" + probe_script(request) + ") > /tmp/runtime-versions 2>&1"],
                deadline,
                cancelled,
            )
            receipt["probe_exit_code"] = probe_code
            if probe_code != 0:
                receipt["status"] = "environment_incompatible"
            else:
                versions = _read_file(
                    container,
                    "/tmp",
                    "runtime-versions",
                    request.profile,
                    client,
                    4096,
                    truncate=True,
                )
                receipt["runtime_versions"] = versions[:4096].decode("utf-8", errors="replace")
                code = _execute(
                    container,
                    client,
                    ["/bin/sh", "-c", bootstrap, "sandbox", "/work/" + request.cwd, *request.argv],
                    deadline,
                    cancelled,
                )
                receipt["exit_code"] = code
                receipt["status"] = "passed" if code == 0 else "failed"
                try:
                    output = _read_file(
                        container,
                        "/tmp",
                        "validation-output",
                        request.profile,
                        client,
                        MAX_LOG,
                        truncate=True,
                    )
                    receipt["logs_truncated"] = len(output) > MAX_LOG
                    log = redact(output[:MAX_LOG].decode("utf-8", errors="replace"))
                    (directory / "output.log").write_text(log)
                    receipt["log_sha256"] = hashlib.sha256(log.encode()).hexdigest()
                except Exception as error:
                    receipt["log_error"] = redact(str(error))[:2000]
                    if receipt["status"] == "passed":
                        receipt["status"] = "collection_error"
                if request.artifacts and receipt["status"] in {"passed", "failed"}:
                    try:
                        receipt["artifacts"] = _collect(
                            container, directory, request.artifacts, request.profile, client
                        )
                    except Exception as error:
                        receipt["artifact_error"] = redact(str(error))[:2000]
                        if receipt["status"] == "passed":
                            receipt["status"] = "artifact_error"

    except (TimeoutError, SnapshotCancelledError) as error:
        receipt.update(
            status="cancelled" if isinstance(error, SnapshotCancelledError) else "timeout",
            error=str(error),
        )
    except Exception as error:
        receipt.update(
            status=abort.get("reason")
            or ("cancelled" if (directory / "cancel-requested").exists() else "error"),
            error=redact(str(error))[:2000],
        )
    except KeyboardInterrupt:
        receipt["status"] = "cancelled"
    finally:
        if container is not None:
            try:
                container.remove(force=True, v=True)
                receipt["cleanup_complete"] = True
            except Exception as error:
                receipt["cleanup_error"] = redact(str(error))[:2000]
                if receipt["status"] == "passed":
                    receipt["status"] = "cleanup_failed"
        else:
            receipt["cleanup_complete"] = True
        monitor_stop.set()
        watcher.join(timeout=12)
        if abort.get("reason"):
            receipt["status"] = abort["reason"]
        receipt["duration_ms"] = int((time.monotonic() - started) * 1000)
        receipt["finished_at"] = time.time()
        _write(directory, receipt)
        if owned and client is not None:
            client.close()
    return receipt


def verify(
    request: ValidationRequest,
    store: Path,
    run_id: str,
    *,
    client: Any = None,
    workspace_fd: int | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Revalidate historical proof, never imply a live process was restored."""
    receipt = read_run(store, run_id)
    reasons = []
    if receipt["request_sha256"] != request_hash(request):
        reasons.append("request_changed")
    if (
        receipt.get("snapshot", {}).get("sha256")
        != capture_bounded(
            Path(request.workspace),
            deadline=time.monotonic() + request.timeout_seconds,
            workspace_fd=workspace_fd,
            cancelled=lambda: cancel_event is not None and cancel_event.is_set(),
        )["sha256"]
    ):
        reasons.append("snapshot_changed")
    owned = client is None
    try:
        client = client or docker_client()
        if client.images.get(request.image).id != receipt.get("image_id"):
            reasons.append("image_changed")
    finally:
        if owned and client is not None:
            client.close()
    for artifact in receipt.get("artifacts", []):
        name = artifact["name"]
        if not re.fullmatch(r"artifact-\d+-\d+", name):
            reasons.append("artifact_path_invalid")
            continue
        path = store / run_id / name
        if (
            path.is_symlink()
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]
        ):
            reasons.append("artifact_changed")
    log = store / run_id / "output.log"
    if (
        log.is_symlink()
        or not log.is_file()
        or hashlib.sha256(log.read_bytes()).hexdigest() != receipt.get("log_sha256")
    ):
        reasons.append("log_changed")
    if [item.get("requested_path") for item in receipt.get("artifacts", [])] != request.artifacts:
        reasons.append("artifacts_incomplete")
    if receipt.get("status") != "passed" or receipt.get("cleanup_complete") is not True:
        reasons.append("run_not_accepted")
    return {
        "schema_version": SCHEMA,
        "run_id": run_id,
        "valid": not reasons,
        "reasons": reasons,
        "continuity": "historical_proof_only",
        "receipt": receipt,
    }


def recover(store: Path, run_id: str, *, client: Any = None) -> dict[str, Any]:
    """Explicit recovery after owner process loss; never sweep unrelated resources."""
    receipt = read_run(store, run_id)
    if receipt["status"] not in {"preparing", "running"} and receipt.get("cleanup_complete"):
        return receipt
    owned = client is None
    try:
        client = client or docker_client()
        try:
            container = client.containers.get(
                receipt.get("container_id") or "snipara-validation-" + run_id
            )
        except Exception as error:
            from docker.errors import NotFound

            if not isinstance(error, NotFound):
                raise
        else:
            if container.labels.get(LABEL) != run_id:
                raise ValueError("container ownership mismatch")
            container.remove(force=True, v=True)
        if receipt.get("snapshot_path"):
            snapshot = secure_path(Path(receipt["snapshot_path"]))
            expected_parent = Path(tempfile.gettempdir()).resolve()
            if snapshot.parent != expected_parent or not snapshot.name.startswith(
                "snipara-validation-" + run_id + "-"
            ):
                raise ValueError("snapshot recovery ownership mismatch")
            if snapshot.exists():
                shutil.rmtree(snapshot)
        receipt.update(status="interrupted", cleanup_complete=True, recovered_at=time.time())
        _write(store / run_id, receipt)
        return receipt
    finally:
        if owned and client is not None:
            client.close()
