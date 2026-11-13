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


def test_validation_cli_does_not_load_project_dotenv(tmp_path):
    from unittest.mock import patch

    from typer.testing import CliRunner

    from rlm.cli.main import app

    request_file = tmp_path / "request.json"
    request_file.write_text(request().model_dump_json())
    with (
        patch("rlm.cli.main._load_project_env") as load_env,
        patch("rlm.validation.environment.gate", return_value={"status": "needs_user_choice"}),
    ):
        result = CliRunner().invoke(
            app, ["validation", "gate", "--request", str(request_file), "--scope", "lot"]
        )
    assert result.exit_code == 2, result.output
    load_env.assert_not_called()


def test_mcp_config_does_not_import_repository_environment(tmp_path, monkeypatch):
    import os

    from rlm.core.config import load_config

    monkeypatch.chdir(tmp_path)
    for name in [
        "DOCKER_HOST",
        "SNIPARA_SANDBOX_VALIDATION_WORKSPACE",
        "RLM_TRUST_LEVEL",
        "SNIPARA_SANDBOX_TRUST_LEVEL",
    ]:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "DOCKER_HOST=tcp://untrusted.invalid:2375\nSNIPARA_SANDBOX_VALIDATION_WORKSPACE=/\nRLM_TRUST_LEVEL=local\n"
    )
    config = load_config(project_env=False)
    assert config.trust_level == "sandboxed"
    assert "DOCKER_HOST" not in os.environ
    assert "SNIPARA_SANDBOX_VALIDATION_WORKSPACE" not in os.environ


def test_cli_reports_an_unusable_request_as_one_line(tmp_path):
    from typer.testing import CliRunner

    from rlm.validation.cli import app

    runner = CliRunner()
    empty = tmp_path / "empty.json"
    empty.write_text("{}")
    result = runner.invoke(app, ["gate", "--request", str(empty), "--scope", "lot:a"])
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "Invalid validation request empty.json:" in result.output
    assert "Field required" in result.output

    broken = tmp_path / "broken.json"
    broken.write_text("not json")
    result = runner.invoke(app, ["inspect", "--request", str(broken)])
    assert result.exit_code == 2
    assert "Invalid validation request broken.json:" in result.output

    large = tmp_path / "large.json"
    large.write_text(" " * 65537)
    result = runner.invoke(app, ["run", "--request", str(large)])
    assert result.exit_code == 2
    assert "request exceeds 64 KiB" in result.output
