from unittest.mock import patch

import pytest

from rlm.validation.mcp import handle, validation_tools


def test_public_validation_tools_expose_contract_without_account():
    tools = {tool.name: tool for tool in validation_tools()}
    assert len(tools) == 6
    assert (
        tools["validation_run"].inputSchema["properties"]["request"]["additionalProperties"]
        is False
    )


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
