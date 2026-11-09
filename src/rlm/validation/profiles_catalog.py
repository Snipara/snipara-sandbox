"""Reviewable dependency profiles and pure request inspection; never execute or install."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from rlm import __version__
from rlm.validation.models import ValidationRequest
from rlm.validation.runner import request_hash

PROFILES: dict[str, dict[str, Any]] = {
    "python-ci": {
        "description": "Prepared Python tests, types, lint and wheel-building tools.",
        "runtime_profile": "python",
        "requirements": {
            "python_modules": ["pytest", "mypy", "ruff", "build", "hatchling"],
            "commands": ["python"],
        },
        "default_argv": ["python", "-m", "pytest", "-q"],
        "suggested_checks": [
            ["python", "-m", "pytest", "-q"],
            ["python", "-m", "mypy", "src"],
            ["python", "-m", "ruff", "check", "--no-cache", "src", "tests"],
            ["python", "-m", "build", "--wheel", "--no-isolation"],
        ],
    },
    "node-ci": {
        "description": "Prepared project-locked Node tests, TypeScript, lint and package build tools.",
        "runtime_profile": "node",
        "requirements": {
            "node_modules": ["typescript", "eslint", "tsup"],
            "commands": ["node", "npm", "git", "tar"],
        },
        "default_argv": ["npm", "test"],
        "suggested_checks": [
            ["npm", "test"],
            ["npm", "run", "type-check"],
            ["npm", "run", "lint"],
            ["npm", "run", "build"],
        ],
    },
}


def validation_profiles() -> dict[str, Any]:
    return {
        "schema_version": "snipara.validation.profiles.v1",
        "package_version": __version__,
        "profiles": copy.deepcopy(PROFILES),
        "execution_authorized": False,
        "requirements_qualified": False,
        "installation_performed": False,
        "note": "Profiles declare required tools. Supply a separately prepared immutable image; run still checks the actual tools.",
    }


def prepare_request(
    profile: str,
    workspace: str,
    image: str,
    argv: list[str] | None = None,
) -> ValidationRequest:
    if profile not in PROFILES:
        raise ValueError("Unknown dependency profile; use validation profiles")
    if not re.fullmatch(r"(?:[^\s]+@)?sha256:[a-f0-9]{64}", image):
        raise ValueError("Prepared profiles require an immutable sha256 image reference")
    directory = Path(workspace)
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError("Workspace must be an existing absolute directory")
    selected = PROFILES[profile]
    return ValidationRequest.model_validate(
        {
            "workspace": str(directory.resolve(strict=True)),
            "image": image,
            "profile": selected["runtime_profile"],
            "argv": selected["default_argv"] if argv is None else argv,
            "requirements": copy.deepcopy(selected["requirements"]),
            "timeout_seconds": 300,
            "cpus": 2.0,
            "memory_mb": 2048,
            "pids": 128,
            "disk_mb": 2048,
        }
    )


def inspect_request(request: ValidationRequest) -> dict[str, Any]:
    return {
        "schema_version": "snipara.validation.inspection.v1",
        "package_version": __version__,
        "request_sha256": request_hash(request),
        "canonical_request": request.identity_payload(),
        "supported_operations": [
            "profiles",
            "prepare",
            "inspect",
            "gate",
            "diagnose",
            "run",
            "verify",
            "status",
            "cancel",
            "recover",
        ],
        "execution_authorized": False,
        "requirements_qualified": False,
        "docker_contacted": False,
    }
