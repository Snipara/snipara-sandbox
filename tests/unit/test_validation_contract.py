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
