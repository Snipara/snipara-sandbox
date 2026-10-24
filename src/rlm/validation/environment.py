"""Explicit, scoped operator choices; absence never authorizes a host fallback."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from rlm.validation.identity import scope_identity
from rlm.validation.models import ValidationRequest
from rlm.validation.paths import secure_path
from rlm.validation.runner import canonical, diagnose
from rlm.validation.state import StateDirectory

PROMPT = "Snipara Sandbox n'est pas disponible pour ces tests. Souhaitez-vous utiliser un autre environnement, installer/configurer Sandbox, ou continuer sans Sandbox ?"
CHOICES = ["alternative", "install", "local", "skip"]


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
    if acknowledged is not True:
        raise ValueError("explicit user choice acknowledgement required")
    if choice not in CHOICES:
        raise ValueError("unsupported choice")
    if not isinstance(environment, str) or len(environment) > 256:
        raise ValueError("environment must be a bounded string")
    if choice == "alternative" and not environment.strip():
        raise ValueError("name the alternative environment")
    now = time.time()
    value = {
        "schema_version": "snipara.validation.choice.v1",
        "scope_sha256": scope_identity(request, scope),
        "choice": choice,
        "environment": environment,
        "authority": "operator_asserted",
        "created_at": now,
        "expires_at": now + 86400,
    }
    path = secure_path(choice_path(request, scope))
    with StateDirectory.open(path.parent, create=True) as directory:
        directory.write(path.name, canonical(value))
    return value


def valid_choice(value: Any, identity: str) -> bool:
    now = time.time()
    return (
        isinstance(value, dict)
        and value.get("schema_version") == "snipara.validation.choice.v1"
        and value.get("scope_sha256") == identity
        and value.get("authority") == "operator_asserted"
        and isinstance(value.get("choice"), str)
        and value["choice"] in CHOICES
        and isinstance(value.get("environment"), str)
        and len(value["environment"]) <= 256
        and (value["choice"] != "alternative" or bool(value["environment"].strip()))
        and type(value.get("created_at")) in (int, float)
        and math.isfinite(value["created_at"])
        and type(value.get("expires_at")) in (int, float)
        and math.isfinite(value["expires_at"])
        and value["expires_at"] > now
        and value["created_at"] <= now + 60
        and 0 < value["expires_at"] - value["created_at"] <= 86401
    )


def gate(
    request: ValidationRequest, scope: str, *, readiness: dict[str, Any] | None = None
) -> dict[str, Any]:
    identity = scope_identity(request, scope)
    choice = None
    invalid_choice = False
    try:
        path = secure_path(choice_path(request, scope))
        with StateDirectory.open(path.parent) as directory:
            value = json.loads(directory.read(path.name, 65536))
        if valid_choice(value, identity):
            choice = value
        else:
            invalid_choice = True
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, OverflowError):
        invalid_choice = True
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
    readiness = (
        {"ready": False, "reason": "saved_choice_invalid_or_expired"}
        if invalid_choice
        else (diagnose(request) if readiness is None else readiness)
    )
    if readiness.get("ready") is True:
        return {
            "status": "sandbox_ready",
            "execution_authorized_by_gate": False,
            "scope_sha256": identity,
            "sandbox_used": False,
            "readiness": readiness,
        }
    return {
        "status": "needs_user_choice",
        "sandbox_used": False,
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
