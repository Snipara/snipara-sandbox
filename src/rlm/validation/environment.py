"""Explicit, scoped operator choices; absence never authorizes a host fallback."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from rlm.validation.models import ValidationRequest
from rlm.validation.runner import canonical, diagnose

PROMPT = "Snipara Sandbox n'est pas disponible pour ces tests. Souhaitez-vous utiliser un autre environnement, installer/configurer Sandbox, ou continuer sans Sandbox ?"
CHOICES = ["alternative", "install", "local", "skip"]


def scope_identity(request: ValidationRequest, scope: str) -> str:
    if not scope or len(scope) > 256:
        raise ValueError("explicit bounded workflow scope required")
    context = request.model_dump(exclude={"argv", "artifacts"})
    context["workspace"] = str(Path(request.workspace).resolve(strict=True))
    return hashlib.sha256(canonical({"scope": scope, "context": context})).hexdigest()


def choice_path(request: ValidationRequest, scope: str) -> Path:
    return (
        Path(request.workspace)
        / ".snipara/validation-choices"
        / (scope_identity(request, scope) + ".json")
    )


def record_choice(
    request: ValidationRequest,
    scope: str,
    choice: str,
    *,
    acknowledged: bool,
    environment: str = "",
) -> dict[str, Any]:
    if not acknowledged:
        raise ValueError("explicit user choice acknowledgement required")
    if choice not in CHOICES:
        raise ValueError("unsupported choice")
    if choice == "alternative" and not environment.strip():
        raise ValueError("name the alternative environment")
    value = {
        "schema_version": "snipara.validation.choice.v1",
        "scope_sha256": scope_identity(request, scope),
        "choice": choice,
        "environment": environment[:256],
        "authority": "operator_asserted",
        "created_at": time.time(),
        "expires_at": time.time() + 86400,
    }
    path = choice_path(request, scope)
    if path.parent.is_symlink() or (path.parent.parent).is_symlink() or path.is_symlink():
        raise ValueError("symlink decision store refused")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(canonical(value))
    path.chmod(0o600)
    return value


def gate(
    request: ValidationRequest, scope: str, *, readiness: dict[str, Any] | None = None
) -> dict[str, Any]:
    identity = scope_identity(request, scope)
    path = choice_path(request, scope)
    choice = None
    if path.exists():
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("symlink decision store refused")
        value = json.loads(path.read_text())
        if (
            value.get("scope_sha256") == identity
            and value.get("expires_at", 0) > time.time()
            and value.get("choice") in CHOICES
        ):
            choice = value
    # A saved choice remains visible even if readiness changes; do not switch environments silently.
    if choice and choice["choice"] in {"local", "alternative", "skip"}:
        return {
            "status": "user_choice_recorded",
            "scope_sha256": identity,
            "choice": choice,
            "sandbox_used": False,
            "proof_requirement_waived": False,
            "execution_authorized_by_gate": False,
        }
    readiness = diagnose(request) if readiness is None else readiness
    if readiness.get("ready") is True:
        return {
            "status": "sandbox_ready",
            "scope_sha256": identity,
            "sandbox_used": False,
            "readiness": readiness,
        }
    return {
        "status": "needs_user_choice",
        "scope_sha256": identity,
        "prompt": PROMPT,
        "choices": [
            {"id": "alternative", "label": "Utiliser un autre environnement de test"},
            {"id": "install", "label": "Installer ou configurer Snipara Sandbox"},
            {"id": "local", "label": "Continuer sans Sandbox avec tests locaux"},
            {"id": "skip", "label": "Continuer sans tests, sans lever les exigences de preuve"},
        ],
        "readiness": readiness,
        "previous_choice": choice,
        "execution_authorized_by_gate": False,
    }
