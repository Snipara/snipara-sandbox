"""Optional coordination never substitutes an unscoped execution after failure."""

import json
import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from rlm.validation.coordinator import coordinated_run
from rlm.validation.models import ValidationRequest
from rlm.validation.snapshot import pin_directory


@pytest.fixture
def lane(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    fd = pin_directory(source)
    request = ValidationRequest(
        workspace=str(source), argv=["python", "-V"], image="sha256:" + "a" * 64
    )
    try:
        yield tmp_path, request, fd
    finally:
        os.close(fd)


def execute(lane, **kwargs):
    root, req, fd = lane
    return coordinated_run(
        req,
        root / ".snipara/validation",
        root=root,
        scope="workflow/lot",
        workspace_fd=fd,
        **kwargs,
    )


def test_missing_optional_coordinator_is_actionable_without_execution(lane):
    with (
        patch("rlm.validation.coordinator.shutil.which", return_value=None),
        patch("rlm.validation.coordinator._execute") as launch,
    ):
        result = execute(lane)
    assert result["reason_code"] == "coordinator_missing"
    launch.assert_not_called()


def test_exact_private_request_root_store_fd_and_filtered_environment(lane):
    root, req, fd = lane
    receipt = {
        "status": "passed",
        "schema_version": "snipara.sandbox.validation.v1",
        "run_id": "a" * 32,
        "backend": "docker",
        "cleanup_complete": True,
        "exit_code": 0,
        "work_reuse": {"state": "reused"},
    }
    calls = []

    def launched(argv, **options):
        calls.append(argv)
        assert options["root"] == root and options["workspace_fd"] == fd
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "snipara-orchestrator 1.15.0\n", "")
        copied = Path(argv[argv.index("--request") + 1])
        assert (
            copied.read_bytes()
            == json.dumps(
                req.identity_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        )
        assert stat.S_IMODE(copied.stat().st_mode) == 0o600
        assert argv[argv.index("--coordination-root") + 1] == str(root)
        assert argv[argv.index("--store") + 1] == str(root / ".snipara/validation")
        assert argv[-1] == "--no-reuse"
        return subprocess.CompletedProcess(argv, 0, json.dumps(receipt), "")

    with (
        patch("rlm.validation.coordinator.shutil.which", return_value="/installed/tool"),
        patch("rlm.validation.coordinator._execute", side_effect=launched),
    ):
        assert execute(lane, no_reuse=True) == receipt
    assert len(calls) == 2


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        "[]",
        '{"status":"passed"}',
        '{"status":"passed","work_reuse":{"state":"needs_review"}}',
    ],
)
def test_ambiguous_launch_is_never_retried_or_raw_fallback(lane, response):
    with (
        patch("rlm.validation.coordinator.shutil.which", return_value="/installed/tool"),
        patch(
            "rlm.validation.coordinator._execute",
            side_effect=[
                subprocess.CompletedProcess([], 0, "snipara-orchestrator 1.15.0\n", ""),
                subprocess.CompletedProcess([], 0, response, ""),
            ],
        ) as launch,
    ):
        result = execute(lane)
    assert result["reason_code"] == "coordinator_execution_uncertain"
    assert launch.call_count == 2


def test_workspace_symlink_substitution_refused_before_dispatch(lane):
    root, req, fd = lane
    Path(req.workspace).rename(root / "original")
    Path(req.workspace).symlink_to(root / "original", target_is_directory=True)
    with patch("rlm.validation.coordinator._execute") as launch:
        result = execute(lane)
    assert result["status"] == "validation_contract_invalid"
    launch.assert_not_called()


def test_store_cannot_escape_operator_root(lane):
    root, req, fd = lane
    with patch("rlm.validation.coordinator._execute") as launch:
        result = coordinated_run(
            req, root.parent / "outside", root=root, scope="lot", workspace_fd=fd
        )
    assert result["status"] == "validation_contract_invalid"
    launch.assert_not_called()


def test_bounded_bridge_passes_only_pinned_descriptor_and_transport_environment(lane, monkeypatch):
    import sys

    from rlm.validation.coordinator import _execute

    root, _, fd = lane
    monkeypatch.setenv("OPENAI_API_KEY", "never-forward-this-provider-key")
    code = (
        "import json,os,sys,stat; print(json.dumps({'directory':stat.S_ISDIR(os.fstat(int(sys.argv[1])).st_mode),"
        "'provider':os.environ.get('OPENAI_API_KEY')}))"
    )
    result = _execute(
        [sys.executable, "-c", code, str(fd)],
        root=root,
        timeout=3,
        workspace_fd=fd,
        cancel_event=None,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"directory": True, "provider": None}


def test_bounded_bridge_times_out_without_relaunch(lane):
    import sys

    from rlm.validation.coordinator import _execute

    root, _, fd = lane
    with pytest.raises(TimeoutError):
        _execute(
            [sys.executable, "-c", "import time; time.sleep(20)"],
            root=root,
            timeout=0.1,
            workspace_fd=fd,
            cancel_event=None,
        )
