"""Profile preparation is a pure, bounded request builder, never an executor."""

import json

import pytest
from typer.testing import CliRunner

from rlm.validation.cli import app
from rlm.validation.models import ValidationRequest
from rlm.validation.profiles_catalog import inspect_request, prepare_request, validation_profiles


def test_complete_profiles_are_copied_and_explicit_about_unqualified_tools():
    catalog = validation_profiles()
    assert set(catalog["profiles"]) == {"python-ci", "node-ci"}
    assert catalog["requirements_qualified"] is False
    assert catalog["installation_performed"] is False
    catalog["profiles"]["python-ci"]["requirements"]["python_modules"].clear()
    assert (
        "build" in validation_profiles()["profiles"]["python-ci"]["requirements"]["python_modules"]
    )


@pytest.mark.parametrize("profile", ["python-ci", "node-ci"])
def test_prepare_builds_an_immutable_request_without_executing(tmp_path, profile):
    request = prepare_request(profile, str(tmp_path), "sha256:" + "a" * 64)
    assert request.workspace == str(tmp_path.resolve())
    assert request.timeout_seconds == 300
    assert request.requirements.model_dump()
    assert inspect_request(request)["execution_authorized"] is False


@pytest.mark.parametrize("image", ["node:latest", "sha256:bad", "-image", ""])
def test_prepare_rejects_unpinned_images(tmp_path, image):
    with pytest.raises(ValueError, match="immutable"):
        prepare_request("python-ci", str(tmp_path), image)


def test_inspection_normalizes_defaults_and_json_formatting(tmp_path):
    raw = {"workspace": str(tmp_path), "image": "sha256:" + "a" * 64, "argv": ["python", "-V"]}
    first = inspect_request(ValidationRequest.model_validate(raw))
    second = inspect_request(
        ValidationRequest.model_validate_json(
            json.dumps(ValidationRequest.model_validate(raw).model_dump(), indent=4)
        )
    )
    assert first["request_sha256"] == second["request_sha256"]
    assert first["canonical_request"] == second["canonical_request"]
    assert first["docker_contacted"] is False


def test_cli_preparation_never_overwrites_and_inspection_never_needs_docker(tmp_path):
    runner = CliRunner()
    path = tmp_path / "request.json"
    args = [
        "prepare",
        "--profile",
        "python-ci",
        "--workspace",
        str(tmp_path),
        "--image",
        "sha256:" + "a" * 64,
        "--output",
        str(path),
        "--",
        "python",
        "-V",
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["argv"] == ["python", "-V"]
    before = path.read_bytes()
    assert runner.invoke(app, args).exit_code != 0
    assert path.read_bytes() == before
    inspected = runner.invoke(app, ["inspect", "--request", str(path)])
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.output)["docker_contacted"] is False
