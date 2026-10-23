import hashlib
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
        workspace=str(project), argv=["python", "test.py"], image="python:local"
    )
    client = Mock()
    client.images.get.return_value.id = "sha256:one"
    (directory / "artifact-0-0").write_bytes(b"ok")
    (directory / "output.log").write_bytes(b"pass")
    receipt = {
        "schema_version": "snipara.sandbox.validation.v1",
        "request_sha256": request_hash(request),
        "snapshot": capture(project),
        "image_id": "sha256:one",
        "status": "passed",
        "cleanup_complete": True,
        "log_sha256": hashlib.sha256(b"pass").hexdigest(),
        "artifacts": [{"name": "artifact-0-0", "sha256": hashlib.sha256(b"ok").hexdigest()}],
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
