"""Unseen and malformed requests must fail before touching a repository."""

import pytest
from pydantic import ValidationError

from rlm.validation.models import ValidationRequest


def request(**changes):
    return ValidationRequest.model_validate(
        dict(workspace="/tmp/project", argv=["python", "-V"], image="python:3.11-slim", **changes)
    )


def test_contract_is_provider_independent():
    assert request().schema_version == "snipara.sandbox.validation.v1"
    assert request().timeout_seconds == 120


@pytest.mark.parametrize(
    "changes",
    [
        {"cwd": "../other"},
        {"cwd": "/etc"},
        {"artifacts": ["a/../../b"]},
        {"artifacts": ["C:\\secret"]},
        {"timeout_seconds": 0},
        {"disk_mb": 10000},
        {"unknown": True},
    ],
)
def test_invalid_scopes_and_limits(changes):
    with pytest.raises(ValidationError):
        request(**changes)


def test_deterministic_validation_does_not_load_inference_stack():
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import rlm.validation.runner; assert 'litellm' not in sys.modules",
        ],
        env={**os.environ, "PYTHONPATH": "src"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("cpus", [1e-12, 0.001, 0.009999])
def test_cpu_quota_cannot_round_to_unlimited(cpus):
    with pytest.raises(ValidationError):
        request(cpus=cpus)


def test_minimum_cpu_quota_stays_positive():
    assert int(request(cpus=0.01).cpus * 1_000_000_000) == 10_000_000
