"""Portable scope identity: UTF-8 JSON arrays of strings, independent of float encoders."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from rlm.validation.models import ValidationRequest
from rlm.validation.paths import canonical_system_path


def scope_identity(request: ValidationRequest, scope: str) -> str:
    if not scope or len(scope) > 256:
        raise ValueError("explicit bounded workflow scope required")
    parts = [
        "snipara.validation.scope.v2",
        scope,
        request.schema_version,
        str(canonical_system_path(Path(request.workspace))),
        request.profile,
        request.image,
        request.cwd,
        str(request.timeout_seconds),
        str(int(request.cpus * 1_000_000_000)),
        str(request.memory_mb),
        str(request.pids),
        str(request.disk_mb),
    ]
    if any(request.requirements.model_dump().values()):
        parts[0] = "snipara.validation.scope.v3"
        for names in (
            request.requirements.python_modules,
            request.requirements.node_modules,
            request.requirements.commands,
        ):
            parts.append(json.dumps(names, ensure_ascii=False, separators=(",", ":")))
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
