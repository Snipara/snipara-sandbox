"""Required host capabilities and a single budget across writable tmpfs mounts."""

from __future__ import annotations

from typing import Any

from rlm.validation.models import ValidationRequest


def require_capabilities(client: Any) -> dict[str, Any]:
    info = {key.lower(): value for key, value in client.info().items()}
    required = ["memorylimit", "cpucfsquota", "pidslimit", "swaplimit"]
    if info.get("ostype") != "linux" or any(info.get(name) is not True for name in required):
        raise ValueError("Docker cannot attest required Linux CPU/memory/PID/swap limits")
    return dict.fromkeys(required, True)


def disk_allocation(request: ValidationRequest) -> dict[str, int]:
    temporary = min(64, request.disk_mb // 4)
    shared = min(64, request.disk_mb // 4)
    return {
        "total_mb": request.disk_mb,
        "workspace_mb": request.disk_mb - temporary - shared,
        "tmp_mb": temporary,
        "shm_mb": shared,
    }
