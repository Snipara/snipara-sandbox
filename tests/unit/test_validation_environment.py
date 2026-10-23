import json

import pytest

from rlm.validation.environment import choice_path, gate, record_choice
from rlm.validation.models import ValidationRequest


def request(tmp_path):
    return ValidationRequest(
        workspace=str(tmp_path), argv=["node", "test.js"], image="node:local", profile="node"
    )


def test_absent_response_never_authorizes_fallback(tmp_path):
    result = gate(request(tmp_path), "lot1", readiness={"ready": False})
    assert result["status"] == "needs_user_choice"
    assert result["execution_authorized_by_gate"] is False
    assert len(result["choices"]) == 4  # Third UI option splits local tests from no tests.


@pytest.mark.parametrize("choice", ["local", "alternative", "skip", "install"])
def test_choices_are_explicit_scoped_and_preserved(tmp_path, choice):
    req = request(tmp_path)
    with pytest.raises(ValueError):
        record_choice(req, "lot1", choice, acknowledged=False)
    record_choice(req, "lot1", choice, acknowledged=True, environment="existing-ci")
    value = gate(req, "lot1", readiness={"ready": False})
    assert value["status"] == (
        "needs_user_choice" if choice == "install" else "user_choice_recorded"
    )
    assert gate(req, "lot2", readiness={"ready": False})["status"] == "needs_user_choice"
    assert (
        gate(req.model_copy(update={"memory_mb": 4096}), "lot1", readiness={"ready": False})[
            "status"
        ]
        == "needs_user_choice"
    )
    if choice != "install":
        assert value["proof_requirement_waived"] is False


def test_expired_choice_and_other_project_do_not_inherit(tmp_path):
    req = request(tmp_path)
    record_choice(req, "lot", "local", acknowledged=True)
    path = choice_path(req, "lot")
    value = json.loads(path.read_text())
    value["expires_at"] = 0
    path.write_text(json.dumps(value))
    assert gate(req, "lot", readiness={"ready": False})["status"] == "needs_user_choice"
    other = tmp_path / "other"
    other.mkdir()
    assert (
        gate(req.model_copy(update={"workspace": str(other)}), "lot", readiness={"ready": False})[
            "status"
        ]
        == "needs_user_choice"
    )


def test_portable_scope_matches_published_cross_language_vectors():
    import json
    from pathlib import Path

    from rlm.validation.identity import scope_identity
    from rlm.validation.models import ValidationRequest

    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "contracts/validation-scope-v2.json").read_text()
    )
    for case in fixture["cases"]:
        assert (
            scope_identity(ValidationRequest.model_validate(case["request"]), case["scope"])
            == case["sha256"]
        )


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": None},
        {"authority": None},
        {"created_at": None},
        {"environment": None},
        {"environment": ""},
        {"expires_at": float("inf")},
        {"expires_at": "future"},
        {"created_at": True},
        {"expires_at": True},
    ],
)
def test_standalone_gate_refuses_malformed_saved_choice_even_if_docker_ready(tmp_path, change):
    from unittest.mock import patch

    req = request(tmp_path)
    value = record_choice(req, "lot", "alternative", acknowledged=True, environment="reviewed-ci")
    value.update(change)
    path = choice_path(req, "lot")
    path.write_text(json.dumps(value))
    before = path.read_bytes()
    with patch("rlm.validation.environment.diagnose") as diagnose:
        result = gate(req, "lot", readiness={"ready": True})
    assert result["status"] == "needs_user_choice"
    assert result["execution_authorized_by_gate"] is False
    assert path.read_bytes() == before
    diagnose.assert_not_called()


def test_incomplete_nonobject_and_excessive_lifetime_choices_cannot_be_reused(tmp_path):
    import time

    req = request(tmp_path)
    good = record_choice(req, "lot", "alternative", acknowledged=True, environment="reviewed-ci")
    path = choice_path(req, "lot")
    for value in [
        [],
        {
            "scope_sha256": good["scope_sha256"],
            "choice": "alternative",
            "expires_at": time.time() + 60,
        },
        {**good, "expires_at": time.time() + 86400 * 365},
        {**good, "created_at": time.time() + 3600, "expires_at": time.time() + 7200},
    ]:
        path.write_text(json.dumps(value))
        assert gate(req, "lot", readiness={"ready": True})["status"] == "needs_user_choice"
    path.write_text(json.dumps(good))
    assert gate(req, "lot", readiness={"ready": True})["status"] == "user_choice_recorded"
