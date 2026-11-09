"""A gate is structural readiness; only an actual run qualifies requirements."""

from unittest.mock import Mock, patch

import pytest

from rlm.validation.environment import gate, record_choice
from rlm.validation.models import ValidationRequest
from rlm.validation.runner import readiness


def client():
    result = Mock()
    result.images.get.return_value.id = "sha256:prepared"
    result.images.get.return_value.attrs = {"Config": {}}
    result.info.return_value = {
        "OSType": "linux",
        "MemoryLimit": True,
        "CpuCfsQuota": True,
        "PidsLimit": True,
        "SwapLimit": True,
    }
    return result


def request(tmp_path, **changes):
    return ValidationRequest(
        workspace=str(tmp_path), argv=["python", "-V"], image="prepared", **changes
    )


def test_structural_readiness_never_captures_or_starts_a_container(tmp_path):
    daemon = client()
    req = request(tmp_path, requirements={"python_modules": ["uninstalled_dependency"]})
    with (
        patch("rlm.validation.runner.capture_bounded", side_effect=AssertionError),
        patch("rlm.validation.runner.run", side_effect=AssertionError),
    ):
        report = readiness(req, client=daemon)
    assert report["ready"] is True
    assert report["requirements_qualified"] is False
    assert report["source_snapshot_verified"] is False
    daemon.containers.create.assert_not_called()
    assert set(report["timings_ms"]) == {
        "workspace_structure",
        "docker_ping",
        "image_lookup",
        "enforcement_capability",
    }
    assert all(type(value) is int and value >= 0 for value in report["timings_ms"].values())


@pytest.mark.parametrize(
    "failure,reason",
    [
        ("ping", "docker_unavailable"),
        ("image", "dependency_image_unavailable"),
        ("limits", "enforcement_unsupported"),
    ],
)
def test_readiness_distinguishes_actionable_environment_failures(tmp_path, failure, reason):
    daemon = client()
    if failure == "ping":
        daemon.ping.side_effect = RuntimeError("daemon unavailable token=private")
    elif failure == "image":
        daemon.images.get.side_effect = RuntimeError("missing image")
    else:
        daemon.info.return_value["MemoryLimit"] = False
    report = readiness(request(tmp_path), client=daemon)
    assert report["ready"] is False and report["reason_code"] == reason
    assert "private" not in report.get("reason", "")
    daemon.containers.create.assert_not_called()


def test_workspace_cwd_and_image_volumes_fail_before_execution(tmp_path):
    daemon = client()
    report = readiness(request(tmp_path, cwd="absent"), client=daemon)
    assert report["reason_code"] == "workspace_invalid"
    daemon.ping.assert_not_called()
    daemon.images.get.return_value.attrs = {"Config": {"Volumes": {"/data": {}}}}
    assert readiness(request(tmp_path), client=daemon)["ready"] is False


def test_gate_only_uses_lightweight_readiness_and_preserves_operator_choice(tmp_path):
    req = request(tmp_path)
    with patch(
        "rlm.validation.environment.structural_readiness", return_value={"ready": True}
    ) as probe:
        assert gate(req, "workflow/lot")["status"] == "sandbox_ready"
        probe.assert_called_once()
        record_choice(req, "workflow/lot", "skip", acknowledged=True)
        assert gate(req, "workflow/lot")["status"] == "user_choice_recorded"
        assert probe.call_count == 1


@pytest.mark.parametrize("timeout", [0, 61, True, "30"])
def test_readiness_rejects_invalid_budget_before_docker(tmp_path, timeout):
    with patch("rlm.validation.runner.docker_client") as daemon:
        with pytest.raises(ValueError):
            readiness(request(tmp_path), probe_timeout_seconds=timeout)
    daemon.assert_not_called()
