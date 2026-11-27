"""The MCP refusal for a workspace outside its root says how to change the root."""

import asyncio

from rlm.validation.mcp import handle as handle_validation


def test_outside_root_refusal_names_the_root_and_the_setting(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "elsewhere"
    root.mkdir()
    outside.mkdir()
    request = {"workspace": str(outside), "argv": ["true"], "image": "sha256:" + "a" * 64}
    result = asyncio.run(handle_validation("validation_diagnose", {"request": request}, root))
    text = result.content[0].text
    assert result.is_error is True
    assert "outside operator-configured MCP root" in text
    assert str(root.resolve()) in text
    assert "SNIPARA_SANDBOX_VALIDATION_WORKSPACE" in text
