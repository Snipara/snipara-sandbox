"""`snipara-sandbox validation run --scope` is native: no Orchestrator is consulted."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from rlm.validation import coordinator
from rlm.validation.cli import app
from rlm.validation.models import SCHEMA

SRC = str(Path(__file__).resolve().parents[2] / "src")
ENTRY = "import sys; sys.argv[0]='snipara-sandbox'; from rlm.cli.main import app; app()"
READY = {"status": "sandbox_ready", "readiness": {"ready": True, "backend": "docker"}}


@pytest.fixture
def no_orchestrator(tmp_path, monkeypatch):
    """A PATH without any snipara-orchestrator, and a Popen that refuses one."""
    empty = tmp_path / "bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert shutil.which("snipara-orchestrator") is None
    real = subprocess.Popen

    def popen(argv, *args, **kwargs):
        assert not any("orchestrator" in str(part) for part in argv), argv
        return real(argv, *args, **kwargs)

    with patch("subprocess.Popen", side_effect=popen):
        yield tmp_path


@pytest.fixture
def request_file(tmp_path):
    (tmp_path / ".git").mkdir()
    workspace = tmp_path / "work"
    workspace.mkdir()
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps(
            {"workspace": str(workspace), "argv": ["python", "-V"], "image": "sha256:" + "d" * 64}
        )
    )
    return path


def receipt(run_id, status="passed"):
    return {
        "schema_version": SCHEMA,
        "run_id": run_id,
        "backend": "docker",
        "status": status,
        "cleanup_complete": True,
        "exit_code": 0 if status == "passed" else 1,
        "request_sha256": "e" * 64,
    }


def invoke(*args):
    return CliRunner().invoke(app, ["run", *args])


def test_help_lists_the_companion_contract_options():
    result = subprocess.run(
        [sys.executable, "-c", ENTRY, "validation", "run", "--help"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": SRC, "COLUMNS": "120"},
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    for option in ("--scope", "--store", "--no-reuse"):
        assert re.search(rf"(?:^|\s){option}(?:\s|,|$)", result.stdout, re.M), option


def test_scoped_run_executes_then_reuses_without_orchestrator(no_orchestrator, request_file):
    store = request_file.parent / ".snipara/validation"
    stored = {}

    def run(request, store, **kwargs):
        stored["a" * 32] = receipt("a" * 32)
        return stored["a" * 32]

    def verify(request, store, run_id, **kwargs):
        return {
            "schema_version": SCHEMA,
            "run_id": run_id,
            "valid": True,
            "reasons": [],
            "receipt": stored[run_id],
        }

    args = ["--request", str(request_file), "--scope", "workflow/lot", "--store", str(store)]
    with (
        patch.object(coordinator, "gate", return_value=READY),
        patch.object(coordinator, "run", side_effect=run) as launch,
        patch.object(coordinator, "verify", side_effect=verify),
    ):
        first = invoke(*args)
        second = invoke(*args)
    assert first.exit_code == 0, first.output
    executed = json.loads(first.stdout)
    assert executed["status"] == "passed" and executed["work_reuse"]["state"] == "executed"
    assert executed["schema_version"] == SCHEMA and executed["backend"] == "docker"
    assert executed["cleanup_complete"] is True and executed["exit_code"] == 0
    assert re.fullmatch("[a-f0-9]{32}", executed["run_id"])
    assert second.exit_code == 0
    assert json.loads(second.stdout)["work_reuse"]["state"] == "reused"
    assert launch.call_count == 1


def test_no_reuse_executes_fresh(no_orchestrator, request_file):
    runs = []

    def run(request, store, **kwargs):
        runs.append(1)
        return receipt(f"{len(runs):032x}")

    def verify(request, store, run_id, **kwargs):
        return {
            "schema_version": SCHEMA,
            "run_id": run_id,
            "valid": True,
            "reasons": [],
            "receipt": receipt(run_id),
        }

    args = ["--request", str(request_file), "--scope", "lot", "--no-reuse"]
    with (
        patch.object(coordinator, "gate", return_value=READY),
        patch.object(coordinator, "run", side_effect=run),
        patch.object(coordinator, "verify", side_effect=verify),
    ):
        assert invoke(*args).exit_code == 0
        result = invoke(*args)
    assert result.exit_code == 0 and len(runs) == 2


def test_failed_scoped_run_exits_two_with_receipt(no_orchestrator, request_file):
    with (
        patch.object(coordinator, "gate", return_value=READY),
        patch.object(coordinator, "run", return_value=receipt("b" * 32, "failed")),
    ):
        result = invoke("--request", str(request_file), "--scope", "lot")
    assert result.exit_code == 2
    value = json.loads(result.stdout)
    assert value["status"] == "failed" and value["work_reuse"]["state"] == "executed"


@pytest.mark.parametrize("extra", [["--no-reuse"], ["--coordination-root", "."]])
def test_scope_only_options_require_scope(no_orchestrator, request_file, extra):
    with patch.object(coordinator, "run") as launch:
        result = invoke("--request", str(request_file), *extra)
    launch.assert_not_called()
    assert result.exit_code == 2
    value = json.loads(result.stdout)
    assert value["status"] == "validation_reuse_needs_review"
    assert value["work_reuse"]["state"] == "needs_review" and "run_id" not in value


def test_unusable_scoped_request_is_reported_as_json(no_orchestrator, tmp_path):
    path = tmp_path / "request.json"
    path.write_text("{not json")
    result = invoke("--request", str(path), "--scope", "lot")
    assert result.exit_code == 2
    value = json.loads(result.stdout)
    assert value["reason_code"] == "request_invalid"
    assert value["work_reuse"]["reason"].startswith("Invalid validation request request.json")


def test_request_outside_coordination_root_is_refused(no_orchestrator, request_file):
    root = request_file.parent / "work"
    with patch.object(coordinator, "run") as launch:
        result = invoke(
            "--request", str(request_file), "--scope", "lot", "--coordination-root", str(root)
        )
    launch.assert_not_called()
    assert result.exit_code == 2
    assert json.loads(result.stdout)["status"] == "validation_reuse_needs_review"


def test_real_cli_process_without_orchestrator_or_docker_asks_for_a_choice(request_file):
    """No mocks: the installed entry point answers the scoped contract by itself."""
    empty = request_file.parent / "bin"
    empty.mkdir()
    env = {
        "PATH": str(empty),
        "HOME": str(request_file.parent),
        "PYTHONPATH": SRC,
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            ENTRY,
            "validation",
            "run",
            "--request",
            str(request_file),
            "--scope",
            "workflow/lot",
            "--store",
            str(request_file.parent / ".snipara/validation"),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=request_file.parent,
        timeout=120,
    )
    assert result.returncode == 2, result.stderr
    value = json.loads(result.stdout)
    assert value["status"] == "needs_user_choice"
    assert value["execution_authorized_by_gate"] is False and value["sandbox_used"] is False
    assert value["work_reuse"]["state"] == "needs_review"
    assert value["readiness"]["reason_code"] in {"docker_tool_missing", "docker_unavailable"}
    assert "run_id" not in value and "orchestrator" not in result.stdout + result.stderr
