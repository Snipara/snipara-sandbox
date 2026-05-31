"""Builtin tools for RLM runtime."""

from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rlm.backends.base import Tool

if TYPE_CHECKING:
    from rlm.repl.base import BaseREPL


# Module-level allowed paths (set by get_builtin_tools)
_allowed_paths: list[Path] = []

DEFAULT_FILE_READ_MAX_LINES = 80
ABSOLUTE_FILE_READ_MAX_LINES = 120
DEFAULT_LIST_FILES_MAX_RESULTS = 60
ABSOLUTE_LIST_FILES_MAX_RESULTS = 100
DEFAULT_FILE_SEARCH_MAX_RESULTS = 40
ABSOLUTE_FILE_SEARCH_MAX_RESULTS = 80
ABSOLUTE_FILE_SEARCH_CONTEXT_LINES = 3
BROAD_GLOBS = {"*", "**", "**/*", "./**/*"}
IGNORED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".snipara",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "logs",
    "node_modules",
    "site-packages",
    "venv",
}


def _validate_path(path: str, allowed_paths: list[Path]) -> tuple[Path | None, str | None]:
    """Validate that a path is within allowed directories.

    Args:
        path: Path string to validate
        allowed_paths: List of allowed base paths. If empty, uses current directory.

    Returns:
        Tuple of (resolved_path, error_message). If valid, error is None.
    """
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError) as e:
        return None, f"Invalid path: {e}"

    # Default to current directory if no allowed paths configured
    bases = allowed_paths if allowed_paths else [Path.cwd()]

    for base in bases:
        try:
            base_resolved = base.resolve()
            # Check if resolved path is under base path
            resolved.relative_to(base_resolved)
            return resolved, None
        except ValueError:
            continue

    # Path not under any allowed base
    return None, f"Access denied: path '{path}' is outside allowed directories"


def _clamp_int(value: int, default: int, minimum: int, maximum: int) -> int:
    """Clamp user-provided integer limits to a safe range."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _is_ignored_path(path: Path) -> bool:
    """Return True when a path sits inside a noisy generated/vendor directory."""
    return any(part in IGNORED_DIRS for part in path.parts)


def _is_broad_recursive_search(pattern: str, recursive: bool) -> bool:
    """Detect recursive listings that would enumerate most of the workspace."""
    return recursive and pattern.strip() in BROAD_GLOBS


def get_builtin_tools(repl: BaseREPL, allowed_paths: list[Path] | None = None) -> list[Tool]:
    """Get builtin tools with the given REPL instance.

    Args:
        repl: REPL instance for code execution
        allowed_paths: List of allowed base paths for file operations.
                       If None or empty, defaults to current working directory.

    Returns:
        List of builtin tools
    """
    global _allowed_paths
    _allowed_paths = allowed_paths or []

    return [
        _create_execute_code_tool(repl),
        _create_file_read_tool(),
        _create_file_search_tool(),
        _create_list_files_tool(),
    ]


def _create_execute_code_tool(repl: BaseREPL) -> Tool:
    """Create the execute_code tool."""

    async def execute_code(code: str) -> dict[str, Any]:
        """Execute Python code in the sandboxed REPL.

        Use this tool when you need to:
        - Perform calculations or data processing
        - Parse and analyze files
        - Transform data formats
        - Run algorithms

        The code runs in a restricted environment with limited imports.
        Use the 'result' variable to return a value.

        Args:
            code: Python code to execute

        Returns:
            Dictionary with output, error (if any), and execution time
        """
        result = await repl.execute(code)
        return {
            "output": result.output,
            "error": result.error,
            "execution_time_ms": result.execution_time_ms,
            "success": result.success,
        }

    return Tool(
        name="execute_code",
        description=(
            "Execute Python code in a sandboxed REPL environment. "
            "Use for calculations, data processing, file parsing, and algorithms. "
            "Set the 'result' variable to return a value. "
            "Available modules: json, re, math, datetime, collections, itertools, csv, statistics."
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute. Use 'result = ...' to return a value.",
                }
            },
            "required": ["code"],
        },
        handler=execute_code,
    )


def _create_file_read_tool() -> Tool:
    """Create the file_read tool."""

    async def file_read(
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        max_lines: int = DEFAULT_FILE_READ_MAX_LINES,
    ) -> dict[str, Any]:
        """Read contents of a file.

        Args:
            path: Path to the file to read
            start_line: Line number to start from (1-indexed)
            end_line: Optional line number to end at (inclusive)
            max_lines: Maximum number of lines to return

        Returns:
            Dictionary with file content and metadata
        """
        file_path, error = _validate_path(path, _allowed_paths)
        if error:
            return {"error": error, "content": None}

        assert file_path is not None  # For type checker

        if not file_path.exists():
            return {"error": f"File not found: {path}", "content": None}

        if not file_path.is_file():
            return {"error": f"Not a file: {path}", "content": None}

        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            requested_max_lines = max_lines
            max_lines = _clamp_int(
                max_lines,
                DEFAULT_FILE_READ_MAX_LINES,
                1,
                ABSOLUTE_FILE_READ_MAX_LINES,
            )
            total_lines = len(lines)
            start_idx = max(0, start_line - 1)
            end_idx = end_line if end_line else start_idx + max_lines
            end_idx = min(end_idx, start_idx + max_lines, total_lines)

            selected_lines = lines[start_idx:end_idx]
            content = "".join(selected_lines)
            capped = requested_max_lines != max_lines

            return {
                "content": content,
                "path": str(file_path),
                "start_line": start_idx + 1,
                "end_line": end_idx,
                "total_lines": total_lines,
                "truncated": end_idx < total_lines,
                "max_lines": max_lines,
                "limit_capped": capped,
            }

        except Exception as e:
            return {"error": f"Error reading file: {e}", "content": None}

    return Tool(
        name="file_read",
        description=(
            "Read the contents of a file. "
            "Returns the content along with line numbers and file metadata. "
            "Use start_line and end_line to read specific portions of large files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read",
                },
                "start_line": {
                    "type": "integer",
                    "default": 1,
                    "description": "Line number to start reading from (1-indexed)",
                },
                "end_line": {
                    "type": "integer",
                    "description": "Optional line number to stop reading at (inclusive)",
                },
                "max_lines": {
                    "type": "integer",
                    "default": DEFAULT_FILE_READ_MAX_LINES,
                    "maximum": ABSOLUTE_FILE_READ_MAX_LINES,
                    "description": (
                        "Maximum number of lines to return. Values above "
                        f"{ABSOLUTE_FILE_READ_MAX_LINES} are capped."
                    ),
                },
            },
            "required": ["path"],
        },
        handler=file_read,
    )


def _create_file_search_tool() -> Tool:
    """Create the file_search tool."""

    async def file_search(
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        max_results: int = DEFAULT_FILE_SEARCH_MAX_RESULTS,
        context_lines: int = 0,
        regex: bool = True,
    ) -> dict[str, Any]:
        """Search files for a targeted pattern without listing the whole tree."""
        if not pattern.strip():
            return {"error": "No search pattern provided", "matches": []}

        search_path, error = _validate_path(path, _allowed_paths)
        if error:
            return {"error": error, "matches": []}

        assert search_path is not None

        if not search_path.exists():
            return {"error": f"Path not found: {path}", "matches": []}

        max_results = _clamp_int(
            max_results,
            DEFAULT_FILE_SEARCH_MAX_RESULTS,
            1,
            ABSOLUTE_FILE_SEARCH_MAX_RESULTS,
        )
        context_lines = _clamp_int(
            context_lines,
            0,
            0,
            ABSOLUTE_FILE_SEARCH_CONTEXT_LINES,
        )

        rg_path = shutil.which("rg")
        if rg_path:
            return _run_rg_search(
                rg_path=rg_path,
                search_path=search_path,
                pattern=pattern,
                glob=glob,
                max_results=max_results,
                context_lines=context_lines,
                regex=regex,
            )

        return _run_python_search(
            search_path=search_path,
            pattern=pattern,
            glob=glob,
            max_results=max_results,
            context_lines=context_lines,
            regex=regex,
        )

    return Tool(
        name="file_search",
        description=(
            "Search files for a specific pattern and return matching file paths, "
            "line numbers, and snippets. Prefer this before file_read when you "
            "do not already know the exact file and line range. Generated and "
            "vendor directories are skipped."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Text or regex pattern to search for",
                },
                "path": {
                    "type": "string",
                    "default": ".",
                    "description": "File or directory to search within",
                },
                "glob": {
                    "type": "string",
                    "description": "Optional glob filter, e.g. '*.py' or 'src/**/*.py'",
                },
                "max_results": {
                    "type": "integer",
                    "default": DEFAULT_FILE_SEARCH_MAX_RESULTS,
                    "maximum": ABSOLUTE_FILE_SEARCH_MAX_RESULTS,
                    "description": "Maximum matches to return; large values are capped",
                },
                "context_lines": {
                    "type": "integer",
                    "default": 0,
                    "maximum": ABSOLUTE_FILE_SEARCH_CONTEXT_LINES,
                    "description": "Context lines around each match; capped at 3",
                },
                "regex": {
                    "type": "boolean",
                    "default": True,
                    "description": "Treat pattern as a regex. Set false for literal text.",
                },
            },
            "required": ["pattern"],
        },
        handler=file_search,
    )


def _run_rg_search(
    *,
    rg_path: str,
    search_path: Path,
    pattern: str,
    glob: str | None,
    max_results: int,
    context_lines: int,
    regex: bool,
) -> dict[str, Any]:
    """Run ripgrep for a bounded search."""
    cmd = [
        rg_path,
        "--line-number",
        "--with-filename",
        "--no-heading",
        "--color",
        "never",
        "--max-count",
        str(max_results),
    ]
    for ignored in sorted(IGNORED_DIRS):
        cmd.extend(["--glob", f"!{ignored}/**"])
        cmd.extend(["--glob", f"!**/{ignored}/**"])
    if glob:
        cmd.extend(["--glob", glob])
    if context_lines:
        cmd.extend(["--context", str(context_lines)])
    if not regex:
        cmd.append("--fixed-strings")
    cmd.extend(["--", pattern, str(search_path)])

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": f"Error searching files: {e}", "matches": []}

    if completed.returncode not in {0, 1}:
        return {
            "error": completed.stderr.strip() or "Search failed",
            "matches": [],
        }

    matches = _parse_rg_output(completed.stdout, max_results)
    return {
        "matches": matches,
        "count": len(matches),
        "truncated": len(matches) >= max_results,
        "path": str(search_path),
        "glob": glob,
        "max_results": max_results,
    }


def _parse_rg_output(output: str, max_results: int) -> list[dict[str, Any]]:
    """Parse ripgrep line output into a small structured result set."""
    matches: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line or line == "--":
            continue
        parts = line.split(":", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        path, line_number, text = parts
        if _is_ignored_path(Path(path)):
            continue
        matches.append(
            {
                "path": path,
                "line": int(line_number),
                "text": text[:500],
            }
        )
        if len(matches) >= max_results:
            break
    return matches


def _run_python_search(
    *,
    search_path: Path,
    pattern: str,
    glob: str | None,
    max_results: int,
    context_lines: int,
    regex: bool,
) -> dict[str, Any]:
    """Fallback bounded search when ripgrep is unavailable."""
    try:
        compiled = re.compile(pattern) if regex else None
    except re.error as e:
        return {"error": f"Invalid regex: {e}", "matches": []}

    candidates = [search_path] if search_path.is_file() else search_path.rglob("*")
    matches: list[dict[str, Any]] = []

    for candidate in candidates:
        if len(matches) >= max_results:
            break
        if _is_ignored_path(candidate) or not candidate.is_file():
            continue
        if glob and not fnmatch.fnmatch(str(candidate), glob):
            continue
        try:
            lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for idx, line in enumerate(lines, 1):
            found = bool(compiled.search(line)) if compiled else pattern in line
            if not found:
                continue
            if context_lines:
                start = max(1, idx - context_lines)
                end = min(len(lines), idx + context_lines)
                text = "\n".join(lines[start - 1 : end])
            else:
                text = line
            matches.append(
                {
                    "path": str(candidate),
                    "line": idx,
                    "text": text[:500],
                }
            )
            if len(matches) >= max_results:
                break

    return {
        "matches": matches,
        "count": len(matches),
        "truncated": len(matches) >= max_results,
        "path": str(search_path),
        "glob": glob,
        "max_results": max_results,
    }


def _create_list_files_tool() -> Tool:
    """Create the list_files tool."""

    async def list_files(
        path: str = ".",
        pattern: str = "*",
        recursive: bool = False,
        max_results: int = DEFAULT_LIST_FILES_MAX_RESULTS,
    ) -> dict[str, Any]:
        """List files in a directory.

        Args:
            path: Directory path to list
            pattern: Glob pattern to filter files (e.g., "*.py", "*.md")
            recursive: Whether to search recursively
            max_results: Maximum number of results to return

        Returns:
            Dictionary with list of files and metadata
        """
        dir_path, error = _validate_path(path, _allowed_paths)
        if error:
            return {"error": error, "files": []}

        assert dir_path is not None  # For type checker

        if not dir_path.exists():
            return {"error": f"Path not found: {path}", "files": []}

        if not dir_path.is_dir():
            return {"error": f"Not a directory: {path}", "files": []}

        if _is_broad_recursive_search(pattern, recursive):
            return {
                "error": (
                    "Refusing broad recursive listing. Use file_search with a "
                    "specific pattern or pass a narrower glob such as '*.py'."
                ),
                "files": [],
                "count": 0,
                "truncated": False,
                "directory": str(dir_path),
            }

        try:
            requested_max_results = max_results
            max_results = _clamp_int(
                max_results,
                DEFAULT_LIST_FILES_MAX_RESULTS,
                1,
                ABSOLUTE_LIST_FILES_MAX_RESULTS,
            )
            if recursive:
                matches = list(dir_path.rglob(pattern))
            else:
                matches = list(dir_path.glob(pattern))

            matches = sorted(match for match in matches if not _is_ignored_path(match))
            limited_matches = matches[:max_results]

            files = []
            for match in limited_matches:
                try:
                    stat = match.stat()
                    files.append(
                        {
                            "path": str(match),
                            "name": match.name,
                            "is_dir": match.is_dir(),
                            "size": stat.st_size if match.is_file() else None,
                        }
                    )
                except (OSError, PermissionError):
                    files.append(
                        {
                            "path": str(match),
                            "name": match.name,
                            "is_dir": match.is_dir(),
                            "size": None,
                        }
                    )

            return {
                "files": files,
                "count": len(files),
                "truncated": len(matches) > max_results,
                "directory": str(dir_path),
                "max_results": max_results,
                "limit_capped": requested_max_results != max_results,
            }

        except Exception as e:
            return {"error": f"Error listing files: {e}", "files": []}

    return Tool(
        name="list_files",
        description=(
            "List files in a directory with optional glob pattern filtering. "
            "Use recursive=true to search subdirectories. "
            "Returns file paths, names, sizes, and whether each is a directory."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "default": ".",
                    "description": "Directory path to list",
                },
                "pattern": {
                    "type": "string",
                    "default": "*",
                    "description": "Glob pattern to filter files (e.g., '*.py', '*.md')",
                },
                "recursive": {
                    "type": "boolean",
                    "default": False,
                    "description": "Search subdirectories recursively",
                },
                "max_results": {
                    "type": "integer",
                    "default": DEFAULT_LIST_FILES_MAX_RESULTS,
                    "maximum": ABSOLUTE_LIST_FILES_MAX_RESULTS,
                    "description": "Maximum number of files to return; large values are capped",
                },
            },
        },
        handler=list_files,
    )
