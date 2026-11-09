from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator, ValidationError

from rlm.validation.mcp import handle, validation_tools


def test_public_validation_tools_expose_contract_without_account():
    tools = {tool.name: tool for tool in validation_tools()}
    assert len(tools) == 8
    assert (
        tools["validation_run"].inputSchema["properties"]["request"]["additionalProperties"]
        is False
    )


@pytest.mark.parametrize("name", ["validation_diagnose", "validation_run", "validation_verify"])
def test_complete_mcp_schema_resolves_nested_model_definitions(name, tmp_path):
    tool = next(tool for tool in validation_tools() if tool.name == name)
    schema = tool.inputSchema
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    request = {"workspace": str(tmp_path), "image": "prepared", "argv": ["python", "-V"]}
    args = {"request": request}
    if name == "validation_verify":
        args["run_id"] = "a" * 32
    validator.validate(args)
    request["requirements"] = {"python_modules": ["fastapi"], "commands": ["git"]}
    validator.validate(args)
    request["requirements"]["commands"] = [123]
    with pytest.raises(ValidationError):
        validator.validate(args)
    request["requirements"] = {"install": ["fastapi"]}
    with pytest.raises(ValidationError):
        validator.validate(args)


@pytest.mark.asyncio
async def test_mcp_rejects_other_workspace_before_execution(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with patch("rlm.validation.mcp.run") as execute:
        result = await handle(
            "validation_run",
            {
                "request": {
                    "workspace": str(outside),
                    "argv": ["python", "test.py"],
                    "image": "python:local",
                }
            },
            root,
        )
    assert result.isError
    execute.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_worker_receives_pinned_source_identity(tmp_path):
    from rlm.validation.snapshot import capture

    root = tmp_path / "root"
    root.mkdir()
    source = root / "source"
    source.mkdir()
    (source / "allowed").write_text("yes")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private").write_text("no")

    def execute(request, store, *, cancel_event, workspace_fd):
        source.rename(tmp_path / "moved")
        source.symlink_to(outside, target_is_directory=True)
        snapshot = capture(source, workspace_fd=workspace_fd)
        return {"status": "passed", "files": [item["path"] for item in snapshot["files"]]}

    with patch("rlm.validation.mcp.run", side_effect=execute):
        result = await handle(
            "validation_run",
            {
                "request": {
                    "workspace": str(source),
                    "argv": ["python", "test.py"],
                    "image": "python:local",
                }
            },
            root,
        )
    assert result.structuredContent["files"] == ["allowed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("subtree", [".aws/sso/cache", "source/.docker/config", "node_modules/pkg"])
async def test_mcp_cannot_re_root_excluded_subtrees(tmp_path, subtree):
    source = tmp_path / subtree
    source.mkdir(parents=True)
    with patch("rlm.validation.mcp.run") as execute:
        result = await handle(
            "validation_run",
            {
                "request": {
                    "workspace": str(source),
                    "argv": ["python", "test.py"],
                    "image": "python:local",
                }
            },
            tmp_path,
        )
    assert result.isError
    execute.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_directory_acquisition_closes_late_descriptor(tmp_path):
    import asyncio
    import os
    import threading
    import time

    acquired = []
    started = threading.Event()

    def slow_acquire(*args, **kwargs):
        started.set()
        time.sleep(0.15)
        fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        acquired.append(fd)
        return fd

    with patch("rlm.validation.mcp.pin_directory_bounded", side_effect=slow_acquire):
        task = asyncio.create_task(
            handle(
                "validation_run",
                {
                    "request": {
                        "workspace": str(tmp_path),
                        "argv": ["python", "test.py"],
                        "image": "python:local",
                    }
                },
                tmp_path,
            )
        )
        while not started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(acquired) == 1
    with pytest.raises(OSError):
        os.fstat(acquired[0])


@pytest.mark.asyncio
async def test_scoped_mcp_uses_optional_coordinator_with_pinned_workspace_never_raw(tmp_path):
    import os
    import stat

    called = []

    def coordinate(request, store, *, root, scope, no_reuse, workspace_fd, cancel_event):
        assert root == tmp_path
        assert store == tmp_path / ".snipara/validation"
        assert stat.S_ISDIR(os.fstat(workspace_fd).st_mode)
        called.append((scope, no_reuse))
        return {"status": "validation_reuse_needs_review", "work_reuse": {"state": "needs_review"}}

    with (
        patch("rlm.validation.mcp.coordinated_run", side_effect=coordinate),
        patch("rlm.validation.mcp.run", side_effect=AssertionError),
    ):
        result = await handle(
            "validation_run",
            {
                "request": {
                    "workspace": str(tmp_path),
                    "image": "prepared",
                    "argv": ["python", "-V"],
                },
                "scope": "workflow/lot",
                "no_reuse": True,
            },
            tmp_path,
        )
    assert result.isError and called == [("workflow/lot", True)]
    assert result.structuredContent["coordination_mode"] == "coordinated"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"scope": ""},
        {"scope": " "},
        {"scope": None},
        {"no_reuse": True},
        {"scope": "lot", "no_reuse": "yes"},
    ],
)
async def test_invalid_coordination_arguments_fail_before_dispatch(tmp_path, extra):
    with (
        patch("rlm.validation.mcp.coordinated_run") as coordinate,
        patch("rlm.validation.mcp.run") as raw,
    ):
        result = await handle(
            "validation_run",
            {
                "request": {
                    "workspace": str(tmp_path),
                    "image": "prepared",
                    "argv": ["python", "-V"],
                },
                **extra,
            },
            tmp_path,
        )
    assert result.isError
    coordinate.assert_not_called()
    raw.assert_not_called()


@pytest.mark.asyncio
async def test_profile_metadata_needs_no_workspace_or_docker(tmp_path):
    with patch("rlm.validation.mcp.pin_directory_bounded", side_effect=AssertionError):
        result = await handle("validation_profiles", {}, tmp_path / "does-not-exist")
    assert not result.isError
    assert result.structuredContent["schema_version"] == "snipara.validation.profiles.v1"


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped", [False, True])
async def test_run_coordination_marker_is_response_only_preserving_receipt(tmp_path, scoped):
    receipt = {"status": "passed", "run_id": "a" * 32}
    arguments = {
        "request": {"workspace": str(tmp_path), "image": "prepared", "argv": ["python", "-V"]}
    }
    if scoped:
        arguments["scope"] = "workflow/lot"
    target = "coordinated_run" if scoped else "run"
    with patch("rlm.validation.mcp." + target, return_value=receipt):
        result = await handle("validation_run", arguments, tmp_path)
    assert result.structuredContent["coordination_mode"] == (
        "coordinated" if scoped else "legacy_raw"
    )
    assert receipt == {"status": "passed", "run_id": "a" * 32}


@pytest.mark.asyncio
async def test_verifier_response_has_no_execution_mode_sideband(tmp_path):
    proof = {"valid": True, "run_id": "a" * 32}
    with patch("rlm.validation.mcp.verify", return_value=proof):
        result = await handle(
            "validation_verify",
            {
                "request": {
                    "workspace": str(tmp_path),
                    "image": "prepared",
                    "argv": ["python", "-V"],
                },
                "run_id": "a" * 32,
            },
            tmp_path,
        )
    assert result.structuredContent == proof
    assert "coordination_mode" not in result.structuredContent
