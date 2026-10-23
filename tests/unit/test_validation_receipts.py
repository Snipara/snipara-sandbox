import hashlib
import json
from unittest.mock import Mock

import pytest

from rlm.validation.models import ValidationRequest
from rlm.validation.runner import canonical, read_run, redact_argv, request_hash, verify
from rlm.validation.snapshot import capture


def test_argv_secret_mask_preserves_only_hash_identity():
    assert redact_argv(["python", "--token", "two words", "--api-key=abc"]) == [
        "python",
        "--token",
        "[REDACTED]",
        "--api-key=[REDACTED]",
    ]


def test_receipt_revalidation_detects_sources_commands_images_and_artifacts(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "test.py").write_text("print(1)")
    store = tmp_path / "runs"
    run_id = "a" * 32
    directory = store / run_id
    directory.mkdir(parents=True)
    request = ValidationRequest(
        workspace=str(project),
        argv=["python", "test.py"],
        image="python:local",
        artifacts=["output.txt"],
    )
    client = Mock()
    client.images.get.return_value.id = "sha256:one"
    (directory / "artifact-0-0").write_bytes(b"ok")
    (directory / "output.log").write_bytes(b"pass")
    receipt = {
        "schema_version": "snipara.sandbox.validation.v1",
        "run_id": run_id,
        "request_sha256": request_hash(request),
        "snapshot": capture(project),
        "image_id": "sha256:one",
        "status": "passed",
        "cleanup_complete": True,
        "log_sha256": hashlib.sha256(b"pass").hexdigest(),
        "artifacts": [
            {
                "name": "artifact-0-0",
                "requested_path": "output.txt",
                "sha256": hashlib.sha256(b"ok").hexdigest(),
            }
        ],
    }
    (directory / "receipt.json").write_bytes(canonical(receipt))
    assert verify(request, store, run_id, client=client)["valid"]
    (directory / "artifact-0-0").write_bytes(b"changed")
    assert "artifact_changed" in verify(request, store, run_id, client=client)["reasons"]
    (directory / "output.log").write_bytes(b"changed")
    assert "log_changed" in verify(request, store, run_id, client=client)["reasons"]
    (project / "test.py").write_text("print(2)")
    assert "snapshot_changed" in verify(request, store, run_id, client=client)["reasons"]
    assert (
        "request_changed"
        in verify(
            request.model_copy(update={"argv": ["python", "other.py"]}),
            store,
            run_id,
            client=client,
        )["reasons"]
    )
    client.images.get.return_value.id = "sha256:two"
    assert "image_changed" in verify(request, store, run_id, client=client)["reasons"]


@pytest.mark.parametrize("run_id", ["../escape", "/etc/passwd", "A" * 32, ""])
def test_invalid_run_identity_never_reads_files(tmp_path, run_id):
    with pytest.raises(ValueError):
        read_run(tmp_path, run_id)


def test_recovery_refuses_another_runs_container(tmp_path):
    from rlm.validation.runner import recover

    run_id = "b" * 32
    directory = tmp_path / run_id
    directory.mkdir()
    (directory / "receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "snipara.sandbox.validation.v1",
                "run_id": run_id,
                "status": "running",
                "container_id": "owned-id",
            }
        )
    )
    client = Mock()
    client.containers.get.return_value.labels = {"com.snipara.sandbox.validation": "other"}
    with pytest.raises(ValueError, match="ownership"):
        recover(tmp_path, run_id, client=client)
    client.containers.get.return_value.remove.assert_not_called()


def test_symlinked_store_ancestor_never_creates_external_files(tmp_path):
    from rlm.validation.runner import run

    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".snipara").symlink_to(outside, target_is_directory=True)
    request = ValidationRequest(
        workspace=str(project),
        argv=["python", "test.py"],
        image="python:local",
        artifacts=["output.txt"],
    )
    with pytest.raises(ValueError, match="symlink"):
        run(request, project / ".snipara/validation", client=Mock())
    assert list(outside.iterdir()) == []


def test_receipt_identity_cannot_redirect_a_cancel(tmp_path):
    from rlm.validation.runner import cancel

    run_id = "a" * 32
    folder = tmp_path / run_id
    folder.mkdir()
    (folder / "receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "snipara.sandbox.validation.v1",
                "run_id": "b" * 32,
                "container_id": "other",
                "status": "running",
            }
        )
    )
    client = Mock()
    with pytest.raises(ValueError, match="identity"):
        cancel(tmp_path, run_id, client=client)
    client.containers.get.assert_not_called()


def test_missing_requested_artifact_cannot_verify_as_a_pass(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    request = ValidationRequest(
        workspace=str(project),
        argv=["python", "test.py"],
        image="python:local",
        artifacts=["report.xml"],
    )
    run_id = "c" * 32
    directory = tmp_path / "runs" / run_id
    directory.mkdir(parents=True)
    (directory / "output.log").write_bytes(b"")
    receipt = {
        "schema_version": "snipara.sandbox.validation.v1",
        "run_id": run_id,
        "request_sha256": request_hash(request),
        "snapshot": capture(project),
        "image_id": "sha256:one",
        "status": "passed",
        "cleanup_complete": True,
        "log_sha256": hashlib.sha256(b"").hexdigest(),
        "artifacts": [],
    }
    (directory / "receipt.json").write_bytes(canonical(receipt))
    client = Mock()
    client.images.get.return_value.id = "sha256:one"
    assert (
        "artifacts_incomplete"
        in verify(request, tmp_path / "runs", run_id, client=client)["reasons"]
    )


def test_public_cancel_preparing_persists_before_docker_exists(tmp_path):
    from rlm.validation.runner import cancel

    run_id = "d" * 32
    directory = tmp_path / run_id
    directory.mkdir()
    (directory / "receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "snipara.sandbox.validation.v1",
                "run_id": run_id,
                "status": "preparing",
            }
        )
    )
    client = Mock()
    assert cancel(tmp_path, run_id, client=client)["cancel_requested"] is True
    assert (directory / "cancel-requested").exists()
    client.containers.get.assert_not_called()


def test_ambiguous_docker_create_reconciles_owned_name(tmp_path):
    from unittest.mock import patch

    from rlm.validation.runner import run

    project = tmp_path / "project"
    project.mkdir()
    request = ValidationRequest(
        workspace=str(project), argv=["python", "test.py"], image="python:local"
    )
    client = Mock()
    client.images.get.return_value.attrs = {"Config": {}}
    client.images.get.return_value.id = "sha256:one"

    def lost_response(*args, **kwargs):
        client.containers.get.return_value.labels = kwargs["labels"]
        raise OSError("synthetic lost Docker create response")

    client.containers.create.side_effect = lost_response
    with (
        patch("rlm.validation.runner.require_capabilities", return_value={}),
        patch("rlm.validation.runner.capture_bounded", return_value={}),
    ):
        receipt = run(request, tmp_path / "runs", client=client)
    assert receipt["status"] == "error"
    assert receipt["cleanup_complete"] is True
    client.containers.get.assert_called_with("snipara-validation-" + receipt["run_id"])
    client.containers.get.return_value.remove.assert_called_once_with(force=True, v=True)


def test_recovery_refuses_active_owner_without_overwriting_receipt(tmp_path):
    from rlm.validation.runner import recover
    from rlm.validation.state import StateDirectory

    run_id = "e" * 32
    folder = tmp_path / run_id
    folder.mkdir()
    receipt = {
        "schema_version": "snipara.sandbox.validation.v1",
        "run_id": run_id,
        "status": "preparing",
    }
    (folder / "receipt.json").write_bytes(canonical(receipt))
    with StateDirectory.open(folder) as directory:
        lock = directory.acquire_owner()
        try:
            with pytest.raises(ValueError, match="still active"):
                recover(tmp_path, run_id, client=Mock())
            assert json.loads((folder / "receipt.json").read_text()) == receipt
        finally:
            import os

            os.close(lock)


def test_collection_deadline_survives_stalled_daemon_and_failed_container_kill(
    tmp_path, monkeypatch
):
    import subprocess
    import time

    import rlm.validation.runner as runner

    popen = subprocess.Popen

    def stalled(argv, **kwargs):
        return popen([argv[0], "-I", "-c", "import time; time.sleep(30)"], **kwargs)

    monkeypatch.setattr(runner.subprocess, "Popen", stalled)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="collection deadline"):
        runner._read_file(
            Mock(id="owned"),
            "/work",
            "artifact",
            "python",
            Mock(),
            1024,
            deadline=started + 0.2,
            cancelled=lambda: False,
        )
    assert time.monotonic() - started < 3


def test_entire_control_transaction_deadline_does_not_depend_on_daemon(tmp_path, monkeypatch):
    import subprocess
    import time

    import rlm.validation.runner as runner

    popen = subprocess.Popen

    def stalled(argv, **kwargs):
        return popen([argv[0], "-I", "-c", "import time; time.sleep(30)"], **kwargs)

    monkeypatch.setattr(runner.subprocess, "Popen", stalled)
    project = tmp_path / "project"
    project.mkdir()
    request = ValidationRequest(
        workspace=str(project), argv=["python", "check.py"], image="python:local"
    )
    started = time.monotonic()
    receipt = runner._control_call(
        "run", request=request, store=tmp_path / "runs", timeout_override=0.2
    )
    assert time.monotonic() - started < 3
    assert receipt["status"] == "timeout" and receipt["cleanup_complete"] is False
    assert read_run(tmp_path / "runs", receipt["run_id"])["cleanup_complete"] is False
