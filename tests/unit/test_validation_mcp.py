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
