"""Native scoped validation: one lock per work, reuse only after fresh verification."""

import json
import os
import stat
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from rlm.validation import coordinator
from rlm.validation.coordinator import coordinated_run, reuse_key
from rlm.validation.models import SCHEMA, ValidationRequest
from rlm.validation.snapshot import pin_directory

READY = {
    "status": "sandbox_ready",
    "execution_authorized_by_gate": False,
    "sandbox_used": False,
    "scope_sha256": "f" * 64,
    "readiness": {"schema_version": SCHEMA, "ready": True, "backend": "docker", "image_id": "x"},
}


def passed(run_id="a" * 32, **extra):
    return {
        "schema_version": SCHEMA,
        "run_id": run_id,
        "backend": "docker",
        "status": "passed",
        "cleanup_complete": True,
        "exit_code": 0,
        **extra,
    }


def verified(request, store, run_id, **_):
    return {
        "schema_version": SCHEMA,
        "run_id": run_id,
        "valid": True,
        "reasons": [],
        "receipt": Fake.receipts[run_id],
    }


class Fake:
    """Run/verify doubles that keep receipts in memory like the run store does."""

    receipts: dict = {}

    def __init__(self, receipts=None):
        self.runs = 0
        self.queue = list(receipts or [])
        Fake.receipts = {}

    def run(self, request, store, *, cancel_event=None, workspace_fd=None):
        assert stat.S_ISDIR(os.fstat(workspace_fd).st_mode)
        self.runs += 1
        receipt = self.queue.pop(0) if self.queue else passed(f"{self.runs:032x}")
        Fake.receipts[receipt["run_id"]] = receipt
        return receipt


@pytest.fixture
def lane(tmp_path):
    (tmp_path / ".git").mkdir()
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


def execute(lane, fake, *, gate=READY, verify=verified, **kwargs):
    root, request, fd = lane
    options = {"scope": "workflow/lot", "workspace_fd": fd, **kwargs}
    with (
        patch.object(coordinator, "gate", return_value=gate) as gated,
        patch.object(coordinator, "run", side_effect=fake.run),
        patch.object(coordinator, "verify", side_effect=verify),
    ):
        result = coordinated_run(request, root / ".snipara/validation", **options)
    return result, gated


def reuse_directory(root):
    return root / ".snipara/validation-reuse"


def test_first_run_executes_and_second_reuses_verified_receipt(lane):
    fake = Fake()
    first, _ = execute(lane, fake)
    assert first["status"] == "passed" and first["work_reuse"]["state"] == "executed"
    second, _ = execute(lane, fake)
    assert fake.runs == 1
    assert second["work_reuse"]["state"] == "reused"
    assert second["run_id"] == first["run_id"]
    assert second["work_reuse"]["key"] == first["work_reuse"]["key"]
    assert "execution_authorized_by_gate" not in second


def test_reuse_state_is_private_and_bounded_to_the_repository(lane):
    root, _, _ = lane
    result, _ = execute(lane, Fake())
    directory = reuse_directory(root)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    key = result["work_reuse"]["key"]
    index = directory / f"{key}.json"
    assert stat.S_IMODE(index.stat().st_mode) == 0o600
    assert json.loads(index.read_text())["receipt"]["run_id"] == result["run_id"]
    assert not (directory / f"{key}.pending.json").exists()
    assert not (root / ".snipara/coordinated-requests").exists()


def test_no_reuse_executes_fresh_and_reindexes(lane):
    fake = Fake()
    execute(lane, fake)
    again, _ = execute(lane, fake, no_reuse=True)
    assert fake.runs == 2 and again["work_reuse"]["state"] == "executed"
    assert again["run_id"] == f"{2:032x}"


def test_stale_index_is_not_reused(lane):
    fake = Fake()
    execute(lane, fake)
    later, _ = execute(lane, fake, clock=lambda: time.time() + 86400 * 2)
    assert fake.runs == 2 and later["work_reuse"]["state"] == "executed"


@pytest.mark.parametrize(
    "verification",
    [
        lambda request, store, run_id, **_: {
            "schema_version": SCHEMA,
            "run_id": run_id,
            "valid": False,
            "reasons": ["snapshot_changed"],
            "receipt": Fake.receipts[run_id],
        },
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("run deleted")),
    ],
    ids=["source_changed", "evidence_missing"],
)
def test_candidate_failing_fresh_verification_executes_instead(lane, verification):
    fake = Fake()
    execute(lane, fake)
    calls = []

    def verify(request, store, run_id, **kwargs):
        calls.append(run_id)
        # Only the shortlisted candidate fails; the new receipt verifies.
        return (
            verification(request, store, run_id)
            if len(calls) == 1
            else verified(request, store, run_id)
        )

    result, _ = execute(lane, fake, verify=verify)
    assert fake.runs == 2 and result["work_reuse"]["state"] == "executed"
    assert calls == ["0" * 31 + "1", "0" * 31 + "2"]


def test_failed_run_is_executed_never_indexed(lane):
    root, _, _ = lane
    fake = Fake([passed(status="failed", exit_code=3)])
    result, _ = execute(lane, fake)
    assert result["status"] == "failed" and result["work_reuse"]["state"] == "executed"
    assert not (reuse_directory(root) / (result["work_reuse"]["key"] + ".json")).exists()
    execute(lane, fake)
    assert fake.runs == 2


def test_passed_run_failing_verification_needs_review_and_is_not_indexed(lane):
    root, _, _ = lane

    def rejected(request, store, run_id, **_):
        return {"schema_version": SCHEMA, "run_id": run_id, "valid": False, "reasons": ["x"]}

    result, _ = execute(lane, Fake(), verify=rejected)
    assert result["status"] == "passed" and result["work_reuse"]["state"] == "needs_review"
    assert not (reuse_directory(root) / (result["work_reuse"]["key"] + ".json")).exists()


@pytest.mark.parametrize(
    "gate",
    [
        {"status": "needs_user_choice", "scope_sha256": "f" * 64, "choices": [{"id": "skip"}]},
        {"status": "user_choice_recorded", "scope_sha256": "f" * 64, "choice": {}},
    ],
)
def test_unready_gate_returns_choice_without_execution(lane, gate):
    fake = Fake()
    result, _ = execute(lane, fake, gate=gate)
    assert fake.runs == 0
    assert result["status"] == gate["status"]
    assert result["work_reuse"]["state"] == "needs_review"
    assert "run_id" not in result


def test_identical_work_is_excluded_while_running(lane):
    root, request, fd = lane
    nested = []
    fake = Fake()

    def gate(*args, **kwargs):
        nested.append(coordinated_run(request, root / ".snipara/validation", scope="workflow/lot"))
        return READY

    with (
        patch.object(coordinator, "gate", side_effect=gate),
        patch.object(coordinator, "run", side_effect=fake.run),
        patch.object(coordinator, "verify", side_effect=verified),
    ):
        result = coordinated_run(
            request, root / ".snipara/validation", scope="workflow/lot", workspace_fd=fd
        )
    assert result["work_reuse"]["state"] == "executed" and fake.runs == 1
    assert nested[0]["status"] == "validation_in_progress"
    assert nested[0]["work_reuse"]["state"] == "in_progress"
    assert "run_id" not in nested[0]


def test_other_scope_is_separate_work(lane):
    fake = Fake()
    execute(lane, fake)
    other, _ = execute(lane, fake, scope="workflow/other-lot")
    assert fake.runs == 2 and other["work_reuse"]["state"] == "executed"


def test_uncertain_cleanup_keeps_pending_marker_and_blocks_next_run(lane):
    root, _, _ = lane
    fake = Fake([passed(status="cancelled", cleanup_complete=False)])
    result, _ = execute(lane, fake)
    assert result["work_reuse"]["state"] == "needs_review"
    key = result["work_reuse"]["key"]
    assert (reuse_directory(root) / f"{key}.pending.json").exists()
    blocked, gated = execute(lane, fake)
    assert fake.runs == 1
    gated.assert_not_called()
    assert blocked["status"] == "validation_reuse_needs_review"
    assert blocked["reason_code"] == "previous_execution_uncertain"
    assert key + ".pending.json" in blocked["work_reuse"]["reason"]


def test_run_exception_is_uncertain_and_never_retried(lane):
    root, request, fd = lane
    with (
        patch.object(coordinator, "gate", return_value=READY),
        patch.object(coordinator, "run", side_effect=ValueError("control failed")) as launch,
    ):
        result = coordinated_run(
            request, root / ".snipara/validation", scope="lot", workspace_fd=fd
        )
    launch.assert_called_once()
    assert result["status"] == "validation_reuse_needs_review"
    assert result["reason_code"] == "scoped_execution_uncertain"
    assert (reuse_directory(root) / (result["work_reuse"]["key"] + ".pending.json")).exists()


@pytest.mark.parametrize(
    "change",
    [
        {"image": "python:3.11-slim"},
        {"image": "registry.example/python:3.11"},
    ],
)
def test_mutable_image_is_refused_before_any_state(lane, change):
    root, request, fd = lane
    with patch.object(coordinator, "run") as launch:
        result = coordinated_run(
            request.model_copy(update=change),
            root / ".snipara/validation",
            scope="lot",
            workspace_fd=fd,
        )
    launch.assert_not_called()
    assert result["reason_code"] == "scoped_contract_invalid"
    assert "not a tag" in result["work_reuse"]["reason"]
    assert "docker image inspect" in result["work_reuse"]["reason"]
    assert result["work_reuse"] == {
        "state": "needs_review",
        "key": None,
        "reason": result["work_reuse"]["reason"],
    }
    assert not reuse_directory(root).exists()


@pytest.mark.parametrize("scope", ["", "  ", "x" * 257])
def test_scope_must_be_explicit_and_bounded(lane, scope):
    root, request, fd = lane
    with patch.object(coordinator, "run") as launch:
        result = coordinated_run(
            request, root / ".snipara/validation", scope=scope, workspace_fd=fd
        )
    launch.assert_not_called()
    assert result["status"] == "validation_reuse_needs_review"


def test_digest_pinned_tag_is_immutable(lane):
    root, request, _ = lane
    pinned = request.model_copy(update={"image": "python:3.11@sha256:" + "b" * 64})
    result, _ = execute((root, pinned, lane[2]), Fake())
    assert result["work_reuse"]["state"] == "executed"


def test_workspace_symlink_substitution_refused_before_dispatch(lane):
    root, request, fd = lane
    Path(request.workspace).rename(root / "original")
    Path(request.workspace).symlink_to(root / "original", target_is_directory=True)
    with patch.object(coordinator, "run") as launch:
        result = coordinated_run(
            request, root / ".snipara/validation", root=root, scope="lot", workspace_fd=fd
        )
    assert result["reason_code"] == "scoped_contract_invalid"
    launch.assert_not_called()


def test_store_cannot_escape_operator_root(lane):
    root, request, fd = lane
    with patch.object(coordinator, "run") as launch:
        result = coordinated_run(
            request, root.parent / "outside", root=root, scope="lot", workspace_fd=fd
        )
    assert result["status"] == "validation_reuse_needs_review"
    launch.assert_not_called()


def test_coordination_root_holds_reuse_state(lane):
    root, request, _ = lane
    operator = root / "source"
    (operator / ".snipara").mkdir()
    fake = Fake()
    with (
        patch.object(coordinator, "gate", return_value=READY),
        patch.object(coordinator, "run", side_effect=fake.run),
        patch.object(coordinator, "verify", side_effect=verified),
    ):
        result = coordinated_run(
            request, operator / ".snipara/validation", root=operator, scope="lot"
        )
    assert result["work_reuse"]["state"] == "executed"
    assert reuse_directory(operator).is_dir() and not reuse_directory(root).exists()


def test_shared_reuse_directory_is_refused(lane):
    root, _, _ = lane
    directory = reuse_directory(root)
    directory.mkdir(parents=True)
    directory.chmod(0o755)
    fake = Fake()
    result, _ = execute(lane, fake)
    assert fake.runs == 0 and result["reason_code"] == "scoped_contract_invalid"


def test_key_binds_scope_request_and_implementation(lane):
    root, request, _ = lane
    store = root / ".snipara/validation"
    workspace = Path(request.workspace)
    base = reuse_key(request, "lot", repository=root, workspace=workspace, store=store)
    assert base != reuse_key(request, "lot2", repository=root, workspace=workspace, store=store)
    changed = request.model_copy(update={"argv": ["python", "-c", "1"]})
    assert base != reuse_key(changed, "lot", repository=root, workspace=workspace, store=store)
    with patch.object(coordinator, "_implementation", return_value={"runner.py": "0" * 64}):
        assert base != reuse_key(request, "lot", repository=root, workspace=workspace, store=store)
