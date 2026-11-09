"""Managed native sessions share canonical lots and pinned peers, without raw fallback."""

import hashlib
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from rlm.validation import managed_toolchain as managed
from rlm.validation.coordinator import coordinated_run
from rlm.validation.mcp import handle
from rlm.validation.models import ValidationRequest
from rlm.validation.profiles_catalog import inspect_request
from rlm.validation.snapshot import pin_directory


@pytest.fixture
def bundle(tmp_path):
    base = tmp_path.resolve()
    base.chmod(0o700)
    root = base / "bundle"
    root.mkdir(mode=0o700)
    binaries = root / "bin"
    binaries.mkdir(mode=0o700)
    components = {}
    for name, version in (("companion", "4.2.0"), ("orchestrator", "1.16.0"), ("sandbox", "3.0.0")):
        command = binaries / ("snipara-" + name)
        command.write_bytes(b"#!/usr/bin/env python3\n# synthetic, never dispatched\n")
        command.chmod(0o700)
        components[name] = {
            "version": version,
            "command": str(command),
            "sha256": hashlib.sha256(command.read_bytes()).hexdigest(),
        }
    manifest = {
        "schemaVersion": "snipara.toolchain.v1",
        "bundleId": "qualification",
        "root": str(root),
        "components": components,
        "contracts": {"taskReference": 1, "validation": 1},
        "capabilities": ["validation", "coordination"],
    }
    filename = root / "toolchain.json"
    filename.write_text(json.dumps(manifest), encoding="utf-8")
    filename.chmod(0o600)
    active = base / "active.json"
    active.symlink_to(filename)
    return base, root, manifest, filename, active


def load(bundle):
    _, _, manifest, _, active = bundle
    return managed.load_managed_toolchain(
        active,
        runtime_command=Path(manifest["components"]["sandbox"]["command"]),
        runtime_version="3.0.0",
    )


def activate(bundle, monkeypatch):
    _, root, _, _, active = bundle
    monkeypatch.setattr(managed, "_OPERATOR_MANIFEST", str(active))
    monkeypatch.setattr(managed, "_INITIALIZED", False)
    monkeypatch.setattr(managed, "_PINNED", None)
    monkeypatch.setattr(managed, "_FAILURE", None)
    monkeypatch.setattr(managed.sys, "executable", str(root / "bin/python"))


def test_owned_selection_and_clean_peer_environment(bundle):
    _, _, manifest, filename, _ = bundle
    selected = load(bundle)
    assert selected.manifest_path == filename
    assert (
        str(selected.components["orchestrator"].command)
        == manifest["components"]["orchestrator"]["command"]
    )
    env = selected.environment(
        {"PATH": "/approved", "OPENAI_API_KEY": "synthetic", "RLM_API_KEY": "synthetic"}
    )
    assert env["SNIPARA_TOOLCHAIN_MANIFEST"] == str(filename)
    assert "OPENAI_API_KEY" not in env and "RLM_API_KEY" not in env


def test_live_session_pins_immutable_manifest_across_active_upgrade(bundle, monkeypatch):
    _, _, _, filename, active = bundle
    activate(bundle, monkeypatch)
    first = managed.selected_toolchain()
    active.unlink()
    active.symlink_to(filename.parent / "not-yet-installed.json")
    assert managed.selected_toolchain() is first
    assert first.manifest_path == filename


def test_modified_pinned_component_fails_before_subprocess_dispatch(bundle, monkeypatch):
    _, _, manifest, _, _ = bundle
    activate(bundle, monkeypatch)
    managed.selected_toolchain()
    command = Path(manifest["components"]["orchestrator"]["command"])
    command.write_bytes(command.read_bytes() + b"# changed\n")
    with pytest.raises(managed.ManagedToolchainError, match="changed"):
        managed.selected_toolchain()


def test_stale_sdk_identity_requires_reconnection(bundle):
    _, root, manifest, _, active = bundle
    with pytest.raises(managed.ManagedToolchainError) as failure:
        managed.load_managed_toolchain(
            active, runtime_command=root / "missing-sdk", runtime_version="3.0.0"
        )
    assert failure.value.code == "toolchain_reconnect_required"
    with pytest.raises(managed.ManagedToolchainError) as failure:
        managed.load_managed_toolchain(
            active,
            runtime_command=Path(manifest["components"]["sandbox"]["command"]),
            runtime_version="2.5.0",
        )
    assert failure.value.code == "toolchain_reconnect_required"


@pytest.mark.parametrize("damage", ["hash", "symlink", "mode", "traversal", "root_command"])
def test_untrusted_selection_is_fail_closed(bundle, damage):
    base, _, manifest, filename, _ = bundle
    command = Path(manifest["components"]["orchestrator"]["command"])
    if damage == "hash":
        command.write_bytes(b"changed")
    elif damage == "symlink":
        outside = base / "outside"
        command.rename(outside)
        command.symlink_to(outside)
    elif damage == "mode":
        filename.chmod(0o660)
    elif damage == "traversal":
        manifest["components"]["orchestrator"]["command"] = str(command.parent) + "/../escape"
        filename.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        manifest["components"]["orchestrator"]["command"] = manifest["root"]
        filename.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(managed.ManagedToolchainError):
        load(bundle)


@pytest.mark.asyncio
async def test_managed_omission_and_no_reuse_use_sdk_inspection_lot_never_raw(bundle):
    base, _, _, _, _ = bundle
    request = ValidationRequest(workspace=str(base), image="prepared", argv=["python", "-V"])
    expected = "snipara-validation:" + inspect_request(request)["request_sha256"]
    calls = []

    def coordinate(*args, **kwargs):
        calls.append(kwargs)
        return {"status": "validation_in_progress", "work_reuse": {"state": "in_progress"}}

    with (
        patch("rlm.validation.mcp.selected_toolchain", return_value=load(bundle)),
        patch("rlm.validation.mcp.coordinated_run", side_effect=coordinate),
        patch("rlm.validation.mcp.run", side_effect=AssertionError("raw fallback")),
    ):
        result = await handle(
            "validation_run", {"request": request.model_dump(), "no_reuse": True}, base
        )
    assert result.isError
    assert result.structuredContent["coordination_mode"] == "coordinated"
    assert len(calls) == 1 and calls[0]["scope"] == expected and calls[0]["no_reuse"] is True
    assert calls[0]["root"] == base


@pytest.mark.asyncio
async def test_invalid_managed_selection_never_uses_a_raw_request(bundle):
    base, _, _, _, _ = bundle
    error = managed.ManagedToolchainError(
        "toolchain_manifest_invalid", "synthetic invalid selection"
    )
    with (
        patch("rlm.validation.mcp.selected_toolchain", side_effect=error),
        patch("rlm.validation.mcp.run") as raw,
        patch("rlm.validation.mcp.coordinated_run") as coordinate,
    ):
        result = await handle(
            "validation_run",
            {"request": {"workspace": str(base), "image": "prepared", "argv": ["python", "-V"]}},
            base,
        )
    assert result.isError and result.structuredContent["execution_authorized_by_gate"] is False
    raw.assert_not_called()
    coordinate.assert_not_called()


def test_managed_coordinator_selects_exact_peers_and_manifest_environment(bundle):
    base, _, manifest, filename, _ = bundle
    request = ValidationRequest(workspace=str(base), image="prepared", argv=["python", "-V"])
    fd = pin_directory(base)
    calls = []
    receipt = {
        "status": "passed",
        "schema_version": "snipara.sandbox.validation.v1",
        "run_id": "a" * 32,
        "backend": "docker",
        "cleanup_complete": True,
        "exit_code": 0,
        "work_reuse": {"state": "reused"},
    }

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv[0] == manifest["components"]["orchestrator"]["command"]
        assert kwargs["environment"]["SNIPARA_TOOLCHAIN_MANIFEST"] == str(filename)
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "snipara-orchestrator 1.16.0\n", "")
        assert (
            argv[argv.index("--sandbox-command") + 1]
            == manifest["components"]["sandbox"]["command"]
        )
        return subprocess.CompletedProcess(argv, 0, json.dumps(receipt), "")

    try:
        with (
            patch("rlm.validation.coordinator.selected_toolchain", return_value=load(bundle)),
            patch(
                "rlm.validation.coordinator.shutil.which",
                side_effect=AssertionError("PATH fallback"),
            ),
            patch("rlm.validation.coordinator._execute", side_effect=execute),
        ):
            result = coordinated_run(
                request,
                base / ".snipara/validation",
                root=base,
                scope="snipara-validation:" + "b" * 64,
                workspace_fd=fd,
            )
        assert result == receipt and len(calls) == 2
    finally:
        os.close(fd)
