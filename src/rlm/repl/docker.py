"""Docker-based REPL sandbox with strong isolation."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import io
import json
import os
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    import docker
    from docker.errors import BuildError, ContainerError, ImageNotFound

    DOCKER_AVAILABLE = True
except ImportError:
    docker = None  # type: ignore[assignment]
    DOCKER_AVAILABLE = False

    # Provide stub classes for type checking and testing
    class ContainerError(Exception):  # type: ignore[no-redef]
        """Stub for docker.errors.ContainerError when docker not installed."""

        def __init__(
            self,
            container: object = None,
            exit_status: int = 1,
            command: str = "",
            image: str = "",
            stderr: bytes = b"",
        ):
            self.container = container
            self.exit_status = exit_status
            self.command = command
            self.image = image
            self.stderr = stderr
            super().__init__(f"Container error: {stderr.decode()}")

    class ImageNotFound(Exception):  # type: ignore[no-redef]
        """Stub for docker.errors.ImageNotFound when docker not installed."""

        pass

    class BuildError(Exception):  # type: ignore[no-redef]
        """Stub for docker.errors.BuildError when docker not installed."""

        def __init__(self, reason: str = "", build_log: list[object] | None = None):
            self.reason = reason
            self.build_log = build_log or []
            super().__init__(reason)


from rlm.core.types import REPLResult
from rlm.repl.base import BaseREPL
from rlm.repl.safety import MAX_EXECUTION_TIME, MAX_MEMORY_MB, truncate_output

WORKSPACE_SETUP_NONE = "none"
WORKSPACE_SETUP_PACKAGE = "package"
WORKSPACE_SETUP_TESTS_ONLY = "tests-only"
WORKSPACE_SETUP_DEV = "dev"
WORKSPACE_SETUP_MODES = frozenset(
    {
        WORKSPACE_SETUP_NONE,
        WORKSPACE_SETUP_PACKAGE,
        WORKSPACE_SETUP_TESTS_ONLY,
        WORKSPACE_SETUP_DEV,
    }
)

DOCKER_RUNTIME_ENV = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "HOME": "/tmp",
    "XDG_CACHE_HOME": "/tmp/.cache",
    "PYTHONPYCACHEPREFIX": "/tmp/pycache",
    "PYTEST_ADDOPTS": "--cache-dir=/tmp/pytest-cache",
}

_WORKSPACE_METADATA_FILES = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "uv.lock",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
)

_WORKSPACE_CONTEXT_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "build",
        "dist",
    }
)

_WORKSPACE_CONTEXT_EXCLUDED_GLOBS = (
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".DS_Store",
    ".coverage",
    ".snipara-sandbox-*",
)


def _default_workspace_install_command(workspace_path: Path, setup_mode: str) -> str:
    """Resolve the default dependency install command for a workspace."""
    if setup_mode == WORKSPACE_SETUP_NONE:
        return ""

    pyproject = workspace_path / "pyproject.toml"
    setup_py = workspace_path / "setup.py"
    setup_cfg = workspace_path / "setup.cfg"
    requirements_dev = workspace_path / "requirements-dev.txt"
    requirements_test = workspace_path / "requirements-test.txt"
    requirements = workspace_path / "requirements.txt"

    if setup_mode == WORKSPACE_SETUP_TESTS_ONLY:
        return "python -m pip install pytest pytest-asyncio"
    if setup_mode == WORKSPACE_SETUP_DEV:
        if pyproject.exists() or setup_py.exists() or setup_cfg.exists():
            return 'python -m pip install -e ".[dev]"'
        if requirements_dev.exists():
            return "python -m pip install -r requirements-dev.txt"
        if requirements_test.exists():
            return "python -m pip install -r requirements-test.txt"
        if requirements.exists():
            return "python -m pip install -r requirements.txt pytest"
    elif setup_mode == WORKSPACE_SETUP_PACKAGE:
        if pyproject.exists() or setup_py.exists() or setup_cfg.exists():
            return "python -m pip install -e ."
        if requirements.exists():
            return "python -m pip install -r requirements.txt"
    else:
        raise ValueError(
            f"Unknown docker workspace setup mode: {setup_mode}. "
            f"Available: {', '.join(sorted(WORKSPACE_SETUP_MODES))}"
        )

    raise ValueError(
        f"Cannot infer how to prepare workspace image for {workspace_path}. "
        "Set docker_workspace_install_command explicitly or add standard Python "
        "project metadata such as pyproject.toml or requirements.txt."
    )


def _workspace_dependency_hash(workspace_path: Path) -> str:
    """Hash dependency metadata files to derive a cacheable workspace image tag."""
    digest = hashlib.sha256()

    for filename in _WORKSPACE_METADATA_FILES:
        path = workspace_path / filename
        if not path.exists() or not path.is_file():
            continue
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")

    return digest.hexdigest()[:16]


def _workspace_image_tag(base_image: str, workspace_path: Path, install_command: str) -> str:
    """Compute a deterministic local Docker tag for a prepared workspace image."""
    digest = hashlib.sha256()
    digest.update(base_image.encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(workspace_path.resolve()).encode("utf-8"))
    digest.update(b"\0")
    digest.update(install_command.encode("utf-8"))
    digest.update(b"\0")
    digest.update(_workspace_dependency_hash(workspace_path).encode("utf-8"))
    return f"snipara-sandbox-workspace:{digest.hexdigest()[:16]}"


def _workspace_image_dockerfile(base_image: str, install_command: str) -> str:
    """Generate a Dockerfile that provisions project dependencies into an image."""
    return f"""
FROM {base_image}

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    PIP_NO_CACHE_DIR=1 \\
    PIP_DISABLE_PIP_VERSION_CHECK=1 \\
    HOME=/tmp \\
    XDG_CACHE_HOME=/tmp/.cache \\
    PYTHONPYCACHEPREFIX=/tmp/pycache \\
    PYTEST_ADDOPTS=--cache-dir=/tmp/pytest-cache

WORKDIR /workspace
COPY . /workspace
RUN python -m pip install --upgrade pip && \\
    {install_command}
"""


def _should_exclude_context_path(relative_path: Path) -> bool:
    """Check whether a workspace path should be excluded from the build context."""
    if any(part in _WORKSPACE_CONTEXT_EXCLUDED_DIRS for part in relative_path.parts):
        return True

    relative_str = relative_path.as_posix()
    name = relative_path.name
    return any(
        fnmatch.fnmatch(relative_str, pattern) or fnmatch.fnmatch(name, pattern)
        for pattern in _WORKSPACE_CONTEXT_EXCLUDED_GLOBS
    )


def _build_context_tarfile(
    workspace_path: Path, dockerfile_name: str, dockerfile_content: str
) -> io.BytesIO:
    """Create a sandbox-owned Docker build context.

    This intentionally ignores the project's own `.dockerignore`, because
    production-lean ignore rules often exclude tests and README files that are
    required for editable installs and repo-backed test runs.
    """
    context = io.BytesIO()
    with tarfile.open(fileobj=context, mode="w") as archive:
        dockerfile_bytes = dockerfile_content.encode("utf-8")
        dockerfile_info = tarfile.TarInfo(dockerfile_name)
        dockerfile_info.size = len(dockerfile_bytes)
        dockerfile_info.mode = 0o644
        dockerfile_info.mtime = int(time.time())
        archive.addfile(dockerfile_info, io.BytesIO(dockerfile_bytes))

        for path in sorted(workspace_path.rglob("*")):
            rel_path = path.relative_to(workspace_path)
            if _should_exclude_context_path(rel_path):
                continue

            if path.is_symlink():
                target = os.readlink(path)
                info = tarfile.TarInfo(rel_path.as_posix())
                info.type = tarfile.SYMTYPE
                info.linkname = target
                info.mode = 0o777
                info.mtime = int(time.time())
                archive.addfile(info)
                continue

            archive.add(path, arcname=rel_path.as_posix(), recursive=False)

    context.seek(0)
    return context


def _extract_build_log_lines(build_log: list[object], limit: int = 30) -> list[str]:
    """Extract readable build log lines from Docker build output."""
    lines: list[str] = []
    for entry in build_log:
        if isinstance(entry, dict):
            for key in ("stream", "error", "message"):
                value = entry.get(key)
                if isinstance(value, str) and value.strip():
                    lines.extend(line.rstrip() for line in value.splitlines() if line.strip())
            error_detail = entry.get("errorDetail")
            if isinstance(error_detail, dict):
                message = error_detail.get("message")
                if isinstance(message, str) and message.strip():
                    lines.extend(line.rstrip() for line in message.splitlines() if line.strip())
        elif isinstance(entry, str) and entry.strip():
            lines.extend(line.rstrip() for line in entry.splitlines() if line.strip())

    return lines[-limit:]


def _format_build_error(workspace_path: Path, tag: str, exc: BuildError) -> str:
    """Render a useful workspace-image build failure."""
    log_lines = _extract_build_log_lines(getattr(exc, "build_log", []))
    summary = getattr(exc, "reason", None) or str(exc) or "Docker build failed"

    if log_lines:
        return (
            f"Failed to prepare workspace image '{tag}' for {workspace_path}. "
            f"{summary}\n\nRecent build log:\n" + "\n".join(log_lines)
        )

    return f"Failed to prepare workspace image '{tag}' for {workspace_path}. {summary}"


def _runtime_environment(has_workdir_mount: bool) -> dict[str, str]:
    """Build the runtime environment for Docker executions."""
    env = dict(DOCKER_RUNTIME_ENV)
    if has_workdir_mount:
        env["PYTHONPATH"] = "/workspace"
    return env


def build_workspace_image(
    *,
    base_image: str,
    workspace_path: Path,
    setup_mode: str,
    install_command: str | None = None,
) -> str:
    """Build or reuse a cached Docker image with workspace dependencies installed."""
    if not DOCKER_AVAILABLE:
        raise ImportError(
            "Docker support requires 'docker' package. "
            "Install with: pip install snipara-sandbox[docker]"
        )

    workspace_path = workspace_path.resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        raise ValueError(f"Workspace path does not exist or is not a directory: {workspace_path}")

    resolved_command = install_command or _default_workspace_install_command(
        workspace_path, setup_mode
    )
    tag = _workspace_image_tag(base_image, workspace_path, resolved_command)

    client = docker.from_env()  # type: ignore[attr-defined]
    try:
        client.images.get(tag)
        return tag
    except ImageNotFound:
        pass

    try:
        dockerfile_name = ".snipara-sandbox.Dockerfile"
        dockerfile_content = _workspace_image_dockerfile(base_image, resolved_command)
        context = _build_context_tarfile(workspace_path, dockerfile_name, dockerfile_content)
        client.images.build(
            fileobj=context,
            custom_context=True,
            encoding="utf-8",
            dockerfile=dockerfile_name,
            tag=tag,
            rm=True,
            pull=False,
        )
        return tag
    except BuildError as exc:
        raise RuntimeError(_format_build_error(workspace_path, tag, exc)) from exc


class DockerREPL(BaseREPL):
    """Docker container REPL with strong isolation.

    Executes code in isolated Docker containers with:
    - Network disabled by default
    - Resource limits (CPU, memory)
    - Read-only filesystem mounts
    - Automatic cleanup

    This is the recommended REPL for untrusted inputs.

    Example:
        ```python
        repl = DockerREPL(
            image="python:3.11-slim",
            cpus=1.0,
            memory="512m",
        )
        result = await repl.execute("print(sum(range(100)))")
        print(result.output)  # "4950\\n"
        ```
    """

    def __init__(
        self,
        image: str = "python:3.11-slim",
        cpus: float = 1.0,
        memory: str = f"{MAX_MEMORY_MB}m",
        timeout: int = MAX_EXECUTION_TIME,
        network_disabled: bool = True,
        workdir_mount: Path | None = None,
    ):
        """Initialize the Docker REPL.

        Args:
            image: Docker image to use
            cpus: CPU limit (e.g., 1.0 = 1 CPU)
            memory: Memory limit (e.g., "512m", "1g")
            timeout: Execution timeout in seconds
            network_disabled: Disable network access (recommended)
            workdir_mount: Optional directory to mount read-only at /workspace
        """
        if not DOCKER_AVAILABLE:
            raise ImportError(
                "Docker support requires 'docker' package. "
                "Install with: pip install snipara-sandbox[docker]"
            )

        self.image = image
        self.cpus = cpus
        self.memory = memory
        self.timeout = timeout
        self.network_disabled = network_disabled
        self.workdir_mount = workdir_mount
        self._client: docker.DockerClient | None = None  # type: ignore[name-defined]
        self._context: dict[str, Any] = {}

    def _get_client(self) -> docker.DockerClient:  # type: ignore[name-defined]
        """Get or create Docker client."""
        if self._client is None:
            self._client = docker.from_env()  # type: ignore[attr-defined]
        return self._client

    async def _ensure_image(self) -> None:
        """Ensure the Docker image exists, pulling if needed."""
        client = self._get_client()
        try:
            client.images.get(self.image)
        except ImageNotFound:
            # Pull image asynchronously
            await asyncio.to_thread(client.images.pull, self.image)

    # Sentinel used to identify the metrics trailer line in container output
    _METRICS_PREFIX = "__RLM_METRICS__:"

    def _create_script(self, code: str) -> str:
        """Create the Python script to run in the container."""
        context_json = json.dumps(self._context)

        return f"""
import json
import sys
import resource as _resource

# Inject context
context = json.loads({context_json!r})
result = None

# Capture stdout
import io
_stdout = io.StringIO()
_original_stdout = sys.stdout
sys.stdout = _stdout

try:
    # User code
{self._indent_code(code)}

except Exception as e:
    print(f"{{type(e).__name__}}: {{e}}", file=sys.stderr)
    sys.exit(1)
finally:
    sys.stdout = _original_stdout

# Output
output = _stdout.getvalue()
if output:
    print(output, end="")
if result is not None:
    print(f"result = {{result!r}}")

# Resource metrics trailer
_usage = _resource.getrusage(_resource.RUSAGE_SELF)
_cpu_ms = int((_usage.ru_utime + _usage.ru_stime) * 1000)
_mem_bytes = _usage.ru_maxrss * 1024  # ru_maxrss is in KB on Linux
print(f"__RLM_METRICS__:{{_cpu_ms}}:{{_mem_bytes}}")
"""

    def _indent_code(self, code: str, spaces: int = 4) -> str:
        """Indent code for inclusion in the script."""
        indent = " " * spaces
        return "\n".join(indent + line for line in code.splitlines())

    def _parse_metrics(self, output: str) -> tuple[str, int | None, int | None]:
        """Parse and strip the resource metrics trailer from container output.

        Args:
            output: Raw container output that may contain a metrics trailer line.

        Returns:
            Tuple of (cleaned_output, cpu_time_ms, memory_peak_bytes).
            cpu_time_ms and memory_peak_bytes are None if the trailer is missing or malformed.
        """
        if not output:
            return output, None, None

        # The metrics trailer is always the last line
        lines = output.split("\n")

        # Find and strip the metrics line (last non-empty line)
        last_line_idx = len(lines) - 1
        while last_line_idx >= 0 and not lines[last_line_idx].strip():
            last_line_idx -= 1

        if last_line_idx < 0:
            return output, None, None

        last_line = lines[last_line_idx].strip()
        if not last_line.startswith(self._METRICS_PREFIX):
            return output, None, None

        try:
            parts = last_line[len(self._METRICS_PREFIX) :].split(":")
            cpu_time_ms = int(parts[0])
            memory_peak_bytes = int(parts[1])
        except (IndexError, ValueError):
            return output, None, None

        # Remove the metrics line from output
        cleaned_lines = lines[:last_line_idx] + lines[last_line_idx + 1 :]
        cleaned_output = "\n".join(cleaned_lines)
        # Strip trailing newlines that were added before the metrics line
        cleaned_output = cleaned_output.rstrip("\n")
        if cleaned_output:
            cleaned_output += "\n"

        return cleaned_output, cpu_time_ms, memory_peak_bytes

    async def execute(self, code: str, timeout: int | None = None) -> REPLResult:
        """Execute code in a Docker container.

        Args:
            code: Python code to execute
            timeout: Optional timeout override

        Returns:
            REPLResult with output, error, and timing
        """
        timeout = timeout or self.timeout
        client = self._get_client()

        # Ensure image exists
        await self._ensure_image()

        # Create temporary script file
        script_content = self._create_script(code)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(script_content)
            script_path = Path(f.name)

        try:
            # Build volumes
            has_workdir_mount = self.workdir_mount is not None and self.workdir_mount.exists()
            volumes: dict[str, dict[str, str]] = {
                str(script_path): {"bind": "/code/script.py", "mode": "ro"},
            }
            if has_workdir_mount and self.workdir_mount is not None:
                volumes[str(self.workdir_mount)] = {"bind": "/workspace", "mode": "ro"}

            # Run container
            start_time = asyncio.get_event_loop().time()

            try:
                output = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.containers.run,
                        self.image,
                        command=["python", "/code/script.py"],
                        volumes=volumes,
                        working_dir="/workspace" if has_workdir_mount else "/code",
                        network_disabled=self.network_disabled,
                        environment=_runtime_environment(has_workdir_mount),
                        mem_limit=self.memory,
                        cpu_quota=int(self.cpus * 100000),
                        cpu_period=100000,
                        remove=True,
                        stdout=True,
                        stderr=True,
                    ),
                    timeout=timeout,
                )

                execution_time_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
                output_str = output.decode("utf-8") if isinstance(output, bytes) else str(output)

                # Parse resource metrics before truncating
                output_str, cpu_time_ms, memory_peak_bytes = self._parse_metrics(output_str)
                output_str, truncated = truncate_output(output_str)

                return REPLResult(
                    output=output_str,
                    error=None,
                    execution_time_ms=execution_time_ms,
                    truncated=truncated,
                    cpu_time_ms=cpu_time_ms,
                    memory_peak_bytes=memory_peak_bytes,
                )

            except ContainerError as e:
                execution_time_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
                stderr = e.stderr.decode("utf-8") if e.stderr else str(e)
                return REPLResult(
                    output="",
                    error=stderr.strip(),
                    execution_time_ms=execution_time_ms,
                )

            except asyncio.TimeoutError:
                return REPLResult(
                    output="",
                    error=f"Execution timed out after {timeout}s",
                    execution_time_ms=timeout * 1000,
                )

        finally:
            # Cleanup temp file
            script_path.unlink(missing_ok=True)

    def get_context(self) -> dict[str, Any]:
        """Get the current context."""
        return self._context.copy()

    def set_context(self, key: str, value: Any) -> None:
        """Set a value in the context.

        Note: Values must be JSON-serializable for Docker REPL.
        """
        # Validate JSON-serializable
        try:
            json.dumps({key: value})
        except (TypeError, ValueError) as e:
            raise ValueError(f"Context value must be JSON-serializable: {e}") from e

        self._context[key] = value

    def clear_context(self) -> None:
        """Clear the context."""
        self._context.clear()

    def cleanup(self) -> None:
        """Cleanup Docker client resources."""
        if self._client:
            self._client.close()
            self._client = None
