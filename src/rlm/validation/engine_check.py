"""Check that the selected container engine enforces what a validation job relies on.

Any engine that speaks the Docker Engine API on a local Unix socket can run validation jobs. Accepting the
container options is not the same as enforcing them, so each property is tested from inside a container created
with the settings the runner uses. Nothing is installed, pulled or written outside a temporary directory.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from rlm.validation.limits import require_capabilities

SCHEMA = "snipara.sandbox.engine-check.v1"
LABEL = "dev.snipara.sandbox.engine-check"
SKIPPED_EXIT = 77
MEMORY_MB = 256
PIDS = 128

# (id, what it proves, shell test run inside the container). Exit 0 = honoured, 77 = cannot be tested with this image.
CHECKS: list[tuple[str, str, str]] = [
    ("user", "runs as the unprivileged user 65534", '[ "$(id -u)" = 65534 ]'),
    ("read_only_root", "the root filesystem is read-only", "! touch /probe 2>/dev/null"),
    (
        "work_writable",
        "/work is writable and executable",
        "printf '#!/bin/sh\\nexit 0\\n' > /work/p && chmod +x /work/p && /work/p",
    ),
    (
        "tmp_sized",
        "/tmp is limited to its declared size",
        "! dd if=/dev/zero of=/tmp/fill bs=1M count=48 2>/dev/null; rm -f /tmp/fill; "
        "dd if=/dev/zero of=/tmp/ok bs=1M count=8 2>/dev/null",
    ),
    (
        "input_read_only",
        "the input mount is readable and not writable",
        "[ -r /input/marker ] && ! touch /input/x 2>/dev/null",
    ),
    (
        "no_network",
        "no network route and no active interface besides loopback",
        '[ "$(tail -n +2 /proc/net/route | wc -l)" -eq 0 ] && for i in /sys/class/net/*; do '
        '[ "${i##*/}" = lo ] || [ "$(cat "$i/operstate")" != up ] || exit 1; done',
    ),
    (
        "loopback",
        "loopback accepts a local TCP connection",
        'command -v python3 >/dev/null 2>&1 || exit 77; python3 -c "import socket; s = socket.socket(); '
        "s.bind(('127.0.0.1', 0)); s.listen(1); socket.create_connection(s.getsockname(), 2).close()\"",
    ),
    (
        "init",
        "an init process reaps children (PID 1 is not the job)",
        '[ "$(cat /proc/1/comm)" != sleep ]',
    ),
    (
        "no_capability",
        "no capability is left",
        "grep -q '^CapEff:[[:space:]]*0000000000000000$' /proc/self/status",
    ),
    (
        "no_new_privs",
        "privileges cannot be regained",
        "grep -q '^NoNewPrivs:[[:space:]]*1$' /proc/self/status",
    ),
    (
        "memory_limit",
        "the memory limit is applied",
        f'[ "$(cat /sys/fs/cgroup/memory.max 2>/dev/null || cat /sys/fs/cgroup/memory/memory.limit_in_bytes)" = {MEMORY_MB * 1024 * 1024} ]',
    ),
    (
        "pids_limit",
        "the process limit is applied",
        f'[ "$(cat /sys/fs/cgroup/pids.max 2>/dev/null || cat /sys/fs/cgroup/pids/pids.max)" = {PIDS} ]',
    ),
]


def _engine(client: Any) -> dict[str, Any]:
    info = client.info()
    return {
        "name": info.get("Name"),
        "server_version": info.get("ServerVersion"),
        "os": info.get("OSType"),
        "arch": info.get("Architecture"),
    }


def engine_check(image: str, *, client: Any = None) -> dict[str, Any]:
    """Return ``{"ok", "engine", "image_id", "checks", ...}``; ``ok`` is False as soon as one property is not honoured."""
    if client is None:
        from rlm.validation.runner import docker_client

        client = docker_client()
    report: dict[str, Any] = {
        "schema_version": SCHEMA,
        "backend": "docker",
        "ok": False,
        "checks": [],
    }
    report["engine"] = _engine(client)

    def add(check: str, proves: str, ok: bool | None, detail: str = "") -> None:
        entry: dict[str, Any] = {
            "check": check,
            "proves": proves,
            "status": "skipped" if ok is None else "ok" if ok else "failed",
        }
        if detail:
            entry["detail"] = detail[-300:]
        report["checks"].append(entry)

    try:
        require_capabilities(client)
        add("limits_reported", "the engine reports Linux CPU, memory, PID and swap limits", True)
    except ValueError as error:
        add(
            "limits_reported",
            "the engine reports Linux CPU, memory, PID and swap limits",
            False,
            str(error),
        )
        return report
    report["image_id"] = client.images.get(image).id
    source = Path(tempfile.mkdtemp(prefix="snipara-engine-check-"))
    (source / "marker").write_text("probe\n")
    source.chmod(0o755)
    container = None
    started = time.monotonic()
    try:
        try:
            container = client.containers.create(
                report["image_id"],
                init=True,
                command=["sleep", "120"],
                labels={LABEL: "1"},
                volumes={str(source): {"bind": "/input", "mode": "ro"}},
                tmpfs={
                    "/work": "rw,exec,nosuid,nodev,size=64m,mode=1777",
                    "/tmp": "rw,exec,nosuid,nodev,size=32m,mode=1777",
                },
                read_only=True,
                user="65534:65534",
                network_disabled=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit=f"{MEMORY_MB}m",
                memswap_limit=f"{MEMORY_MB}m",
                nano_cpus=1_000_000_000,
                pids_limit=PIDS,
                shm_size=64 * 1024 * 1024,
                log_config={"Type": "json-file", "Config": {"max-size": "1m", "max-file": "1"}},
                detach=True,
            )
            container.start()
            add("create", "a container with the runner's settings can be created and started", True)
        except Exception as error:  # noqa: BLE001 - any engine refusal is the finding
            add(
                "create",
                "a container with the runner's settings can be created and started",
                False,
                str(error),
            )
            return report
        for check, proves, test in CHECKS:
            execution = client.api.exec_create(container.id, ["sh", "-c", test])
            raw = client.api.exec_start(execution)
            code = client.api.exec_inspect(execution).get("ExitCode")
            detail = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else ""
            add(
                check,
                proves,
                None if code == SKIPPED_EXIT else code == 0,
                "" if code in (0, SKIPPED_EXIT) else detail,
            )
        try:
            container.kill()
            add("kill", "the container can be killed", True)
        except Exception as error:  # noqa: BLE001
            add("kill", "the container can be killed", False, str(error))
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:  # noqa: BLE001 - cleanup must not hide the result
                pass
        shutil.rmtree(source, ignore_errors=True)
    report["seconds"] = round(time.monotonic() - started, 2)
    report["ok"] = all(entry["status"] != "failed" for entry in report["checks"])
    return report
