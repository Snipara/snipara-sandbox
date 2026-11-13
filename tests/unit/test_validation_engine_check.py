from unittest.mock import Mock

from typer.testing import CliRunner

from rlm.validation import engine_check as ec
from rlm.validation.cli import app

INFO = {
    "OSType": "linux",
    "MemoryLimit": True,
    "CpuCfsQuota": True,
    "PidsLimit": True,
    "SwapLimit": True,
    "Name": "engine",
    "ServerVersion": "1.0",
    "Architecture": "aarch64",
}


def fake_client(exit_codes=None, info=None, create_error=None):
    client = Mock()
    client.info.return_value = info or INFO
    client.images.get.return_value = Mock(id="sha256:abc")
    container = Mock(id="c1")
    if create_error:
        client.containers.create.side_effect = create_error
    else:
        client.containers.create.return_value = container
    codes = list(exit_codes if exit_codes is not None else [0] * len(ec.CHECKS))
    client.api.exec_create.side_effect = lambda _id, argv: {"Id": argv[2]}
    client.api.exec_start.return_value = b"why it failed"
    client.api.exec_inspect.side_effect = lambda _execution: {"ExitCode": codes.pop(0)}
    return client, container


def test_container_uses_the_runner_settings_and_is_always_removed():
    client, container = fake_client()
    report = ec.engine_check("img:local", client=client)
    assert report["ok"] is True and report["image_id"] == "sha256:abc"
    assert [c["status"] for c in report["checks"]] == ["ok"] * (len(ec.CHECKS) + 3)
    kwargs = client.containers.create.call_args.kwargs
    assert (
        kwargs["init"] is True
        and kwargs["read_only"] is True
        and kwargs["network_disabled"] is True
    )
    assert kwargs["user"] == "65534:65534" and kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges:true"]
    assert kwargs["mem_limit"] == kwargs["memswap_limit"] == "256m" and kwargs["pids_limit"] == 128
    assert set(kwargs["tmpfs"]) == {"/work", "/tmp"} and "size=32m" in kwargs["tmpfs"]["/tmp"]
    assert list(kwargs["volumes"].values()) == [{"bind": "/input", "mode": "ro"}]
    container.kill.assert_called_once()
    container.remove.assert_called_once_with(force=True)


def test_one_property_not_honoured_fails_the_engine_and_a_missing_tool_is_skipped():
    codes = [0] * len(ec.CHECKS)
    names = [check for check, _, _ in ec.CHECKS]
    codes[names.index("read_only_root")] = 1
    codes[names.index("loopback")] = ec.SKIPPED_EXIT
    client, container = fake_client(codes)
    report = ec.engine_check("img:local", client=client)
    by = {c["check"]: c for c in report["checks"]}
    assert report["ok"] is False
    assert (
        by["read_only_root"]["status"] == "failed"
        and by["read_only_root"]["detail"] == "why it failed"
    )
    assert by["loopback"]["status"] == "skipped" and "detail" not in by["loopback"]
    container.remove.assert_called_once_with(force=True)


def test_an_engine_without_limits_or_that_refuses_the_settings_is_reported_not_raised():
    client, _ = fake_client(info={**INFO, "SwapLimit": False})
    report = ec.engine_check("img:local", client=client)
    assert report["ok"] is False and report["checks"][0]["status"] == "failed"
    client.containers.create.assert_not_called()

    client, _ = fake_client(create_error=RuntimeError("unknown option: init"))
    report = ec.engine_check("img:local", client=client)
    assert report["ok"] is False and report["checks"][-1]["check"] == "create"
    assert "unknown option" in report["checks"][-1]["detail"]


def test_command_exits_2_when_the_engine_fails(monkeypatch):
    monkeypatch.setattr(
        ec, "engine_check", lambda image: {"ok": False, "checks": [], "image": image}
    )
    result = CliRunner().invoke(app, ["engine", "--image", "img:local"])
    assert result.exit_code == 2 and '"ok": false' in result.stdout
    monkeypatch.setattr(ec, "engine_check", lambda image: {"ok": True, "checks": []})
    assert CliRunner().invoke(app, ["engine", "--image", "img:local"]).exit_code == 0
