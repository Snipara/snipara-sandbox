from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator, ValidationError

from rlm.validation.mcp import handle, validation_tools


def test_public_validation_tools_expose_contract_without_account():
    tools = {tool.name: tool for tool in validation_tools()}
    assert len(tools) == 6
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
