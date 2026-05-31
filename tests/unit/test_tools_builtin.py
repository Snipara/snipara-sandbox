"""Tests for builtin tools."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from rlm.tools.builtin import (
    _create_execute_code_tool,
    get_builtin_tools,
)


class TestGetBuiltinTools:
    """Tests for get_builtin_tools function."""

    def test_returns_four_tools(self):
        """Should return list of four builtin tools."""
        mock_repl = MagicMock()
        tools = get_builtin_tools(mock_repl)

        assert len(tools) == 4

    def test_tool_names(self):
        """Should return tools with correct names."""
        mock_repl = MagicMock()
        tools = get_builtin_tools(mock_repl)

        names = [t.name for t in tools]
        assert "execute_code" in names
        assert "file_read" in names
        assert "file_search" in names
        assert "list_files" in names


class TestExecuteCodeTool:
    """Tests for execute_code tool."""

    @pytest.fixture
    def tool(self):
        """Create execute_code tool with mock REPL."""
        mock_repl = MagicMock()
        mock_repl.execute = AsyncMock()
        return _create_execute_code_tool(mock_repl), mock_repl

    def test_tool_name(self, tool):
        """Should have correct name."""
        execute_tool, _ = tool
        assert execute_tool.name == "execute_code"

    def test_tool_parameters(self, tool):
        """Should have correct parameters schema."""
        execute_tool, _ = tool
        params = execute_tool.parameters

        assert params["type"] == "object"
        assert "code" in params["properties"]
        assert "code" in params["required"]

    @pytest.mark.asyncio
    async def test_execute_success(self, tool):
        """Should return success result."""
        execute_tool, mock_repl = tool

        mock_result = MagicMock()
        mock_result.output = "42"
        mock_result.error = None
        mock_result.execution_time_ms = 10
        mock_result.success = True
        mock_repl.execute.return_value = mock_result

        result = await execute_tool.execute(code="print(42)")

        assert result["output"] == "42"
        assert result["error"] is None
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_execute_with_error(self, tool):
        """Should return error result."""
        execute_tool, mock_repl = tool

        mock_result = MagicMock()
        mock_result.output = None
        mock_result.error = "NameError: name 'x' is not defined"
        mock_result.execution_time_ms = 5
        mock_result.success = False
        mock_repl.execute.return_value = mock_result

        result = await execute_tool.execute(code="print(x)")

        assert result["error"] == "NameError: name 'x' is not defined"
        assert result["success"] is False


class TestFileReadTool:
    """Tests for file_read tool."""

    @pytest.fixture
    def tool(self, tmp_path):
        """Create file_read tool with tmp_path allowed."""
        # Get tools with tmp_path as allowed path for testing
        mock_repl = MagicMock()
        tools = get_builtin_tools(mock_repl, allowed_paths=[tmp_path])
        # file_read is the second tool (index 1)
        return tools[1]

    @pytest.fixture
    def test_file(self, tmp_path):
        """Create a test file."""
        file = tmp_path / "test.txt"
        file.write_text("Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n")
        return file

    def test_tool_name(self, tool):
        """Should have correct name."""
        assert tool.name == "file_read"

    def test_tool_parameters(self, tool):
        """Should have correct parameters schema."""
        params = tool.parameters

        assert params["type"] == "object"
        assert "path" in params["properties"]
        assert "start_line" in params["properties"]
        assert "end_line" in params["properties"]
        assert "max_lines" in params["properties"]
        assert "path" in params["required"]

    @pytest.mark.asyncio
    async def test_read_entire_file(self, tool, test_file):
        """Should read entire file."""
        result = await tool.execute(path=str(test_file))

        assert result["content"] == "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n"
        assert result["total_lines"] == 5
        assert "error" not in result or result.get("error") is None

    @pytest.mark.asyncio
    async def test_read_with_start_line(self, tool, test_file):
        """Should read from specific start line."""
        result = await tool.execute(path=str(test_file), start_line=2)

        assert "Line 2" in result["content"]
        assert result["start_line"] == 2

    @pytest.mark.asyncio
    async def test_read_with_end_line(self, tool, test_file):
        """Should read up to specific end line."""
        result = await tool.execute(path=str(test_file), start_line=1, end_line=2)

        assert result["content"] == "Line 1\nLine 2\n"
        assert result["end_line"] == 2

    @pytest.mark.asyncio
    async def test_read_with_max_lines(self, tool, test_file):
        """Should respect max_lines limit."""
        result = await tool.execute(path=str(test_file), max_lines=2)

        lines = result["content"].strip().split("\n")
        assert len(lines) == 2

    @pytest.mark.asyncio
    async def test_read_caps_large_max_lines(self, tool, tmp_path):
        """Should cap large max_lines values."""
        test_file = tmp_path / "large.txt"
        test_file.write_text("".join(f"Line {i}\n" for i in range(200)))

        result = await tool.execute(path=str(test_file), max_lines=10_000)

        lines = result["content"].strip().split("\n")
        assert len(lines) == 120
        assert result["max_lines"] == 120
        assert result["limit_capped"] is True

    @pytest.mark.asyncio
    async def test_file_not_found(self, tool, tmp_path):
        """Should return error for non-existent file."""
        result = await tool.execute(path=str(tmp_path / "nonexistent.txt"))

        assert result["error"] is not None
        assert "not found" in result["error"].lower()
        assert result["content"] is None

    @pytest.mark.asyncio
    async def test_not_a_file(self, tool, tmp_path):
        """Should return error for directory."""
        result = await tool.execute(path=str(tmp_path))

        assert result["error"] is not None
        assert "not a file" in result["error"].lower()
        assert result["content"] is None


class TestFileSearchTool:
    """Tests for file_search tool."""

    @pytest.fixture
    def tool(self, tmp_path):
        """Create file_search tool with tmp_path allowed."""
        mock_repl = MagicMock()
        tools = get_builtin_tools(mock_repl, allowed_paths=[tmp_path])
        return tools[2]

    @pytest.fixture
    def test_dir(self, tmp_path):
        """Create searchable files."""
        (tmp_path / "alpha.py").write_text("def target_function():\n    return 42\n")
        (tmp_path / "beta.txt").write_text("literal target text\n")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "ignored.py").write_text("target_function()\n")
        return tmp_path

    def test_tool_name(self, tool):
        """Should have correct name."""
        assert tool.name == "file_search"

    def test_tool_parameters(self, tool):
        """Should have correct parameters schema."""
        params = tool.parameters

        assert params["type"] == "object"
        assert "pattern" in params["properties"]
        assert "glob" in params["properties"]
        assert "max_results" in params["properties"]
        assert "pattern" in params["required"]

    @pytest.mark.asyncio
    async def test_search_finds_matches(self, tool, test_dir):
        """Should find matching files and line numbers."""
        result = await tool.execute(
            pattern="target_function",
            path=str(test_dir),
            glob="*.py",
        )

        assert result["count"] == 1
        assert result["matches"][0]["line"] == 1
        assert result["matches"][0]["path"].endswith("alpha.py")

    @pytest.mark.asyncio
    async def test_search_supports_literal_mode(self, tool, test_dir):
        """Should support literal searches when regex is false."""
        result = await tool.execute(
            pattern="literal target text",
            path=str(test_dir),
            glob="*.txt",
            regex=False,
        )

        assert result["count"] == 1
        assert result["matches"][0]["path"].endswith("beta.txt")

    @pytest.mark.asyncio
    async def test_search_caps_max_results(self, tool, tmp_path):
        """Should cap large max_results values."""
        for i in range(100):
            (tmp_path / f"file_{i}.txt").write_text("needle\n")

        result = await tool.execute(
            pattern="needle",
            path=str(tmp_path),
            max_results=10_000,
        )

        assert result["count"] == 80
        assert result["truncated"] is True
        assert result["max_results"] == 80

    @pytest.mark.asyncio
    async def test_search_rejects_empty_pattern(self, tool, test_dir):
        """Should reject empty patterns."""
        result = await tool.execute(pattern="", path=str(test_dir))

        assert result["error"] is not None
        assert result["matches"] == []


class TestListFilesTool:
    """Tests for list_files tool."""

    @pytest.fixture
    def tool(self, tmp_path):
        """Create list_files tool with tmp_path allowed."""
        # Get tools with tmp_path as allowed path for testing
        mock_repl = MagicMock()
        tools = get_builtin_tools(mock_repl, allowed_paths=[tmp_path])
        # list_files is the fourth tool (index 3)
        return tools[3]

    @pytest.fixture
    def test_dir(self, tmp_path):
        """Create test directory structure."""
        (tmp_path / "file1.txt").write_text("content")
        (tmp_path / "file2.py").write_text("code")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "file3.txt").write_text("nested")
        return tmp_path

    def test_tool_name(self, tool):
        """Should have correct name."""
        assert tool.name == "list_files"

    def test_tool_parameters(self, tool):
        """Should have correct parameters schema."""
        params = tool.parameters

        assert params["type"] == "object"
        assert "path" in params["properties"]
        assert "pattern" in params["properties"]
        assert "recursive" in params["properties"]
        assert "max_results" in params["properties"]

    @pytest.mark.asyncio
    async def test_list_all_files(self, tool, test_dir):
        """Should list all files in directory."""
        result = await tool.execute(path=str(test_dir))

        assert result["count"] >= 2
        names = [f["name"] for f in result["files"]]
        assert "file1.txt" in names
        assert "file2.py" in names

    @pytest.mark.asyncio
    async def test_list_with_pattern(self, tool, test_dir):
        """Should filter by pattern."""
        result = await tool.execute(path=str(test_dir), pattern="*.txt")

        names = [f["name"] for f in result["files"]]
        assert "file1.txt" in names
        assert "file2.py" not in names

    @pytest.mark.asyncio
    async def test_list_recursive(self, tool, test_dir):
        """Should search recursively."""
        result = await tool.execute(path=str(test_dir), pattern="*.txt", recursive=True)

        names = [f["name"] for f in result["files"]]
        assert "file1.txt" in names
        assert "file3.txt" in names

    @pytest.mark.asyncio
    async def test_list_with_max_results(self, tool, test_dir):
        """Should respect max_results."""
        result = await tool.execute(path=str(test_dir), max_results=1)

        assert result["count"] == 1
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_list_caps_large_max_results(self, tool, test_dir):
        """Should cap large max_results values."""
        for i in range(120):
            (test_dir / f"extra_{i}.txt").write_text("content")

        result = await tool.execute(path=str(test_dir), max_results=10_000)

        assert result["count"] == 100
        assert result["truncated"] is True
        assert result["max_results"] == 100
        assert result["limit_capped"] is True

    @pytest.mark.asyncio
    async def test_list_refuses_broad_recursive_listing(self, tool, test_dir):
        """Should reject broad recursive repository inventory requests."""
        result = await tool.execute(path=str(test_dir), pattern="*", recursive=True)

        assert result["error"] is not None
        assert "broad recursive" in result["error"]
        assert result["files"] == []

    @pytest.mark.asyncio
    async def test_path_not_found(self, tool, tmp_path):
        """Should return error for non-existent path."""
        result = await tool.execute(path=str(tmp_path / "nonexistent"))

        assert result["error"] is not None
        assert "not found" in result["error"].lower()
        assert result["files"] == []

    @pytest.mark.asyncio
    async def test_not_a_directory(self, tool, test_dir):
        """Should return error for file path."""
        file_path = test_dir / "file1.txt"
        result = await tool.execute(path=str(file_path))

        assert result["error"] is not None
        assert "not a directory" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_file_metadata(self, tool, test_dir):
        """Should include file metadata."""
        result = await tool.execute(path=str(test_dir), pattern="file1.txt")

        assert len(result["files"]) == 1
        file_info = result["files"][0]
        assert "path" in file_info
        assert "name" in file_info
        assert "is_dir" in file_info
        assert "size" in file_info
        assert file_info["is_dir"] is False


class TestPathValidation:
    """Tests for path validation security."""

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, tmp_path: Path):
        """Should block path traversal attempts."""
        # Create a test file outside allowed paths
        tools = get_builtin_tools(MagicMock(), allowed_paths=[tmp_path])
        file_read = tools[1]  # file_read tool

        # Try to access parent directory
        result = await file_read.handler(path=str(tmp_path.parent / "etc" / "passwd"))
        assert result.get("error") is not None
        assert (
            "outside allowed" in result["error"].lower()
            or "access denied" in result["error"].lower()
        )
