"""Tests for Docker REPL sandbox."""

import asyncio
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestWorkspaceImagePreparation:
    """Tests for prepared workspace Docker images."""

    def test_default_workspace_install_command_dev_pyproject(self, tmp_path: Path):
        """Should use editable dev install for standard Python projects."""
        from rlm.repl.docker import _default_workspace_install_command

        (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n")

        command = _default_workspace_install_command(tmp_path, "dev")

        assert command == 'python -m pip install -e ".[dev]"'

    def test_default_workspace_install_command_package_requirements(self, tmp_path: Path):
        """Should fall back to requirements for non-package workspaces."""
        from rlm.repl.docker import _default_workspace_install_command

        (tmp_path / "requirements.txt").write_text("pytest\n")

        command = _default_workspace_install_command(tmp_path, "package")

        assert command == "python -m pip install -r requirements.txt"

    def test_default_workspace_install_command_tests_only(self, tmp_path: Path):
        """Should install only the test runner dependencies in tests-only mode."""
        from rlm.repl.docker import _default_workspace_install_command

        command = _default_workspace_install_command(tmp_path, "tests-only")

        assert command == "python -m pip install pytest pytest-asyncio"

    def test_workspace_install_command_requires_project_metadata(self, tmp_path: Path):
        """Should fail clearly when workspace install mode cannot be inferred."""
        from rlm.repl.docker import _default_workspace_install_command

        with pytest.raises(ValueError) as exc_info:
            _default_workspace_install_command(tmp_path, "dev")

        assert "Cannot infer" in str(exc_info.value)

    def test_append_install_extras_adds_scoped_pip_install(self):
        """Should append a scoped pip install so a conftest's deps can be pulled."""
        from rlm.repl.docker import _append_install_extras

        command = _append_install_extras(
            "python -m pip install pytest pytest-asyncio",
            ["fastapi", "pydantic-settings"],
        )

        assert command == (
            "python -m pip install pytest pytest-asyncio && "
            "python -m pip install fastapi pydantic-settings"
        )

    def test_append_install_extras_noop_without_extras(self):
        """Should leave the command untouched when no extras are requested."""
        from rlm.repl.docker import _append_install_extras

        base = "python -m pip install pytest"
        assert _append_install_extras(base, None) == base
        assert _append_install_extras(base, []) == base

    def test_append_install_extras_rejects_shell_metacharacters(self):
        """Should reject extras that could break out of the RUN line."""
        from rlm.repl.docker import _append_install_extras

        with pytest.raises(ValueError) as exc_info:
            _append_install_extras("python -m pip install pytest", ["fastapi; rm -rf /"])

        assert "Invalid install extra" in str(exc_info.value)

    def test_runtime_pytest_addopts_uses_ini_override(self):
        """pytest 9 removed --cache-dir; the runtime must use the -o ini override."""
        from rlm.repl.docker import DOCKER_RUNTIME_ENV, _workspace_image_dockerfile

        assert DOCKER_RUNTIME_ENV["PYTEST_ADDOPTS"] == "-o cache_dir=/tmp/pytest-cache"
        dockerfile = _workspace_image_dockerfile("python:3.11-slim", "python -m pip install pytest")
        assert "--cache-dir=" not in dockerfile
        assert "-o cache_dir=/tmp/pytest-cache" in dockerfile

    def test_build_context_tarfile_ignores_project_dockerignore_behavior(self, tmp_path: Path):
        """Should keep tests and README in the build context while dropping heavy noise."""
        from rlm.repl.docker import _build_context_tarfile

        (tmp_path / "README.md").write_text("demo\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_demo.py").write_text("def test_ok():\n    assert True\n")
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "ignored.txt").write_text("skip me\n")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "ignored.pyc").write_bytes(b"compiled")

        context = _build_context_tarfile(
            tmp_path,
            ".snipara-sandbox.Dockerfile",
            "FROM python:3.11-slim\n",
        )

        with tarfile.open(fileobj=context, mode="r") as archive:
            names = set(archive.getnames())

        assert ".snipara-sandbox.Dockerfile" in names
        assert "README.md" in names
        assert "tests/test_demo.py" in names
        assert ".venv/ignored.txt" not in names
        assert "__pycache__/ignored.pyc" not in names

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_build_workspace_image_reuses_existing_tag(self, mock_docker, tmp_path: Path):
        """Should reuse cached workspace images when the tag already exists."""
        from rlm.repl.docker import build_workspace_image

        (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n")

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()

        tag = build_workspace_image(
            base_image="python:3.11-slim",
            workspace_path=tmp_path,
            setup_mode="dev",
        )

        assert tag.startswith("snipara-sandbox-workspace:")
        mock_client.images.build.assert_not_called()

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_build_workspace_image_builds_missing_tag(self, mock_docker, tmp_path: Path):
        """Should build a workspace image when the cached tag is missing."""
        from rlm.repl.docker import ImageNotFound, build_workspace_image

        (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n")

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.side_effect = ImageNotFound("missing")

        tag = build_workspace_image(
            base_image="python:3.11-slim",
            workspace_path=tmp_path,
            setup_mode="dev",
        )

        assert tag.startswith("snipara-sandbox-workspace:")
        build_kwargs = mock_client.images.build.call_args.kwargs
        assert build_kwargs["custom_context"] is True
        assert build_kwargs["encoding"] == "utf-8"
        assert build_kwargs["dockerfile"] == ".snipara-sandbox.Dockerfile"
        assert hasattr(build_kwargs["fileobj"], "read")
        assert build_kwargs["tag"] == tag

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_build_workspace_image_surfaces_recent_build_logs(self, mock_docker, tmp_path: Path):
        """Should raise a readable error with the recent Docker build log."""
        from rlm.repl.docker import BuildError, ImageNotFound, build_workspace_image

        (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n")

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.side_effect = ImageNotFound("missing")
        mock_client.images.build.side_effect = BuildError(
            "The command '/bin/sh -c pip install' returned a non-zero code: 1",
            build_log=[
                {"stream": "Step 1/3 : FROM python:3.11-slim\n"},
                {"stream": "Step 2/3 : COPY . /workspace\n"},
                {"errorDetail": {"message": "ERROR: Could not find README.md\n"}},
            ],
        )

        with pytest.raises(RuntimeError) as exc_info:
            build_workspace_image(
                base_image="python:3.11-slim",
                workspace_path=tmp_path,
                setup_mode="dev",
            )

        message = str(exc_info.value)
        assert "Failed to prepare workspace image" in message
        assert "Recent build log:" in message
        assert "ERROR: Could not find README.md" in message

    def test_workspace_image_tag_changes_with_dependency_metadata(self, tmp_path: Path):
        """Should invalidate prepared images when dependency metadata changes."""
        from rlm.repl.docker import _workspace_image_tag

        lockfile = tmp_path / "uv.lock"
        lockfile.write_text("version = 1\n")
        tag_before = _workspace_image_tag(
            "python:3.11-slim",
            tmp_path,
            'python -m pip install -e ".[dev]"',
        )

        lockfile.write_text("version = 2\n")
        tag_after = _workspace_image_tag(
            "python:3.11-slim",
            tmp_path,
            'python -m pip install -e ".[dev]"',
        )

        assert tag_before != tag_after


class TestDockerREPLInit:
    """Tests for DockerREPL initialization."""

    def test_raises_import_error_without_docker(self):
        """Should raise ImportError when docker package not available."""
        with patch.dict("sys.modules", {"docker": None}):
            with patch("rlm.repl.docker.DOCKER_AVAILABLE", False):
                from rlm.repl.docker import DockerREPL

                with pytest.raises(ImportError) as exc_info:
                    DockerREPL()
                assert "docker" in str(exc_info.value).lower()

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_default_settings(self, mock_docker):
        """Should use default settings."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()

        assert repl.image == "python:3.11-slim"
        assert repl.cpus == 1.0
        assert repl.network_disabled is True

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_custom_settings(self, mock_docker):
        """Should accept custom settings."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL(
            image="python:3.12",
            cpus=2.0,
            memory="1g",
            timeout=60,
            network_disabled=False,
        )

        assert repl.image == "python:3.12"
        assert repl.cpus == 2.0
        assert repl.memory == "1g"
        assert repl.timeout == 60
        assert repl.network_disabled is False

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_workdir_mount(self, mock_docker, tmp_path):
        """Should accept workdir mount."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL(workdir_mount=tmp_path)

        assert repl.workdir_mount == tmp_path


class TestDockerREPLClient:
    """Tests for Docker client management."""

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_lazy_client_creation(self, mock_docker):
        """Should create client lazily."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()
        assert repl._client is None

        # Access client
        repl._get_client()
        mock_docker.from_env.assert_called_once()

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_client_reuse(self, mock_docker):
        """Should reuse existing client."""
        from rlm.repl.docker import DockerREPL

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client

        repl = DockerREPL()
        client1 = repl._get_client()
        client2 = repl._get_client()

        assert client1 is client2
        mock_docker.from_env.assert_called_once()

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_cleanup(self, mock_docker):
        """Should close client on cleanup."""
        from rlm.repl.docker import DockerREPL

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client

        repl = DockerREPL()
        repl._get_client()
        repl.cleanup()

        mock_client.close.assert_called_once()
        assert repl._client is None


class TestDockerREPLContext:
    """Tests for Docker REPL context management."""

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_empty_context_initially(self, mock_docker):
        """Should start with empty context."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()
        assert repl.get_context() == {}

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_set_context(self, mock_docker):
        """Should set context values."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()
        repl.set_context("key", "value")
        repl.set_context("number", 42)

        context = repl.get_context()
        assert context["key"] == "value"
        assert context["number"] == 42

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_clear_context(self, mock_docker):
        """Should clear context."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()
        repl.set_context("key", "value")
        repl.clear_context()

        assert repl.get_context() == {}

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_get_context_returns_copy(self, mock_docker):
        """Should return a copy of context."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()
        repl.set_context("key", "value")

        context = repl.get_context()
        context["new_key"] = "new_value"

        assert "new_key" not in repl.get_context()

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_set_context_requires_json_serializable(self, mock_docker):
        """Should reject non-JSON-serializable values."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()

        # Lambda is not JSON serializable
        with pytest.raises(ValueError) as exc_info:
            repl.set_context("func", lambda x: x)
        assert "JSON" in str(exc_info.value)


class TestDockerREPLScriptCreation:
    """Tests for script creation."""

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_creates_script_with_code(self, mock_docker):
        """Should create script containing user code."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()
        script = repl._create_script("print('hello')")

        assert "print('hello')" in script

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_creates_script_with_context(self, mock_docker):
        """Should inject context into script."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()
        repl.set_context("data", [1, 2, 3])
        script = repl._create_script("print(context)")

        # Context should be JSON-encoded in script
        assert "json.loads" in script

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_indent_code(self, mock_docker):
        """Should properly indent code."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()
        code = "line1\nline2"
        indented = repl._indent_code(code, spaces=4)

        assert indented == "    line1\n    line2"


class TestDockerREPLExecution:
    """Tests for code execution."""

    @pytest.mark.asyncio
    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    async def test_execute_success(self, mock_docker):
        """Should return success result."""
        from rlm.repl.docker import DockerREPL

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = b"hello\n"

        repl = DockerREPL()
        result = await repl.execute("print('hello')")

        assert result.output == "hello\n"
        assert result.error is None

    @pytest.mark.asyncio
    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    async def test_execute_with_container_error(self, mock_docker):
        """Should handle container errors."""
        from rlm.repl.docker import ContainerError, DockerREPL

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()

        error = ContainerError(
            container=MagicMock(),
            exit_status=1,
            command="python",
            image="python:3.11",
            stderr=b"NameError: name 'x' is not defined",
        )
        mock_client.containers.run.side_effect = error

        repl = DockerREPL()
        result = await repl.execute("print(x)")

        assert result.error is not None
        assert "NameError" in result.error

    @pytest.mark.asyncio
    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    async def test_execute_timeout(self, mock_docker):
        """Should handle execution timeout."""
        from rlm.repl.docker import DockerREPL

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()

        # Make containers.run block forever
        async def slow_run(*args, **kwargs):
            await asyncio.sleep(10)
            return b"done"

        mock_client.containers.run.side_effect = asyncio.TimeoutError()

        repl = DockerREPL(timeout=1)
        result = await repl.execute("import time; time.sleep(10)")

        assert result.error is not None
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    async def test_execute_pulls_missing_image(self, mock_docker):
        """Should pull image if not found."""
        from rlm.repl.docker import DockerREPL, ImageNotFound

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.side_effect = ImageNotFound("not found")
        mock_client.containers.run.return_value = b"output"

        repl = DockerREPL()
        await repl.execute("print('test')")

        mock_client.images.pull.assert_called_once_with("python:3.11-slim")

    @pytest.mark.asyncio
    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    async def test_execute_with_workdir_mount(self, mock_docker, tmp_path):
        """Should mount workdir when specified."""
        from rlm.repl.docker import DockerREPL

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = b"output"

        repl = DockerREPL(workdir_mount=tmp_path)
        await repl.execute("print('test')")

        # Check that volumes include workdir
        call_kwargs = mock_client.containers.run.call_args.kwargs
        volumes = call_kwargs.get("volumes", {})
        assert str(tmp_path) in volumes
        assert call_kwargs["working_dir"] == "/workspace"
        assert call_kwargs["environment"]["PYTHONDONTWRITEBYTECODE"] == "1"
        assert call_kwargs["environment"]["PYTHONPATH"] == "/workspace"

    @pytest.mark.asyncio
    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    async def test_execute_with_missing_workdir_mount_falls_back_to_code(
        self, mock_docker, tmp_path
    ):
        """Should fall back to /code when the configured mount path is missing."""
        from rlm.repl.docker import DockerREPL

        missing_path = tmp_path / "missing-workspace"

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = b"output"

        repl = DockerREPL(workdir_mount=missing_path)
        await repl.execute("print('test')")

        call_kwargs = mock_client.containers.run.call_args.kwargs
        volumes = call_kwargs.get("volumes", {})
        assert str(missing_path) not in volumes
        assert call_kwargs["working_dir"] == "/code"
        assert "PYTHONPATH" not in call_kwargs["environment"]


class TestDockerREPLResourceLimits:
    """Tests for resource limit configuration."""

    @pytest.mark.asyncio
    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    async def test_memory_limit_applied(self, mock_docker):
        """Should apply memory limit."""
        from rlm.repl.docker import DockerREPL

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = b"output"

        repl = DockerREPL(memory="256m")
        await repl.execute("print('test')")

        call_kwargs = mock_client.containers.run.call_args.kwargs
        assert call_kwargs["mem_limit"] == "256m"

    @pytest.mark.asyncio
    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    async def test_cpu_limit_applied(self, mock_docker):
        """Should apply CPU limit."""
        from rlm.repl.docker import DockerREPL

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = b"output"

        repl = DockerREPL(cpus=0.5)
        await repl.execute("print('test')")

        call_kwargs = mock_client.containers.run.call_args.kwargs
        # CPU quota = cpus * 100000
        assert call_kwargs["cpu_quota"] == 50000
        assert call_kwargs["cpu_period"] == 100000

    @pytest.mark.asyncio
    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    async def test_network_disabled(self, mock_docker):
        """Should disable network by default."""
        from rlm.repl.docker import DockerREPL

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = b"output"

        repl = DockerREPL()
        await repl.execute("print('test')")

        call_kwargs = mock_client.containers.run.call_args.kwargs
        assert call_kwargs["network_disabled"] is True

    @pytest.mark.asyncio
    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    async def test_network_enabled(self, mock_docker):
        """Should allow network when configured."""
        from rlm.repl.docker import DockerREPL

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = b"output"

        repl = DockerREPL(network_disabled=False)
        await repl.execute("print('test')")

        call_kwargs = mock_client.containers.run.call_args.kwargs
        assert call_kwargs["network_disabled"] is False

    @pytest.mark.asyncio
    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    async def test_container_auto_removed(self, mock_docker):
        """Should auto-remove container after execution."""
        from rlm.repl.docker import DockerREPL

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = b"output"

        repl = DockerREPL()
        await repl.execute("print('test')")

        call_kwargs = mock_client.containers.run.call_args.kwargs
        assert call_kwargs["remove"] is True


class TestDockerREPLParseMetrics:
    """Tests for resource metrics parsing."""

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_parse_valid_metrics(self, mock_docker):
        """Should parse valid metrics trailer."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()
        output = "hello world\n__RLM_METRICS__:42:1048576\n"

        cleaned, cpu_ms, mem_bytes = repl._parse_metrics(output)

        assert cleaned == "hello world\n"
        assert cpu_ms == 42
        assert mem_bytes == 1048576

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_parse_metrics_no_user_output(self, mock_docker):
        """Should handle metrics with no user output."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()
        output = "__RLM_METRICS__:10:524288\n"

        cleaned, cpu_ms, mem_bytes = repl._parse_metrics(output)

        assert cleaned == ""
        assert cpu_ms == 10
        assert mem_bytes == 524288

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_parse_metrics_missing(self, mock_docker):
        """Should return None when no metrics trailer present."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()
        output = "hello world\n"

        cleaned, cpu_ms, mem_bytes = repl._parse_metrics(output)

        assert cleaned == "hello world\n"
        assert cpu_ms is None
        assert mem_bytes is None

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_parse_metrics_malformed(self, mock_docker):
        """Should return None for malformed metrics."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()
        output = "hello\n__RLM_METRICS__:not_a_number:also_bad\n"

        cleaned, cpu_ms, mem_bytes = repl._parse_metrics(output)

        assert cleaned == "hello\n__RLM_METRICS__:not_a_number:also_bad\n"
        assert cpu_ms is None
        assert mem_bytes is None

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_parse_metrics_empty_output(self, mock_docker):
        """Should handle empty output."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()

        cleaned, cpu_ms, mem_bytes = repl._parse_metrics("")

        assert cleaned == ""
        assert cpu_ms is None
        assert mem_bytes is None

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_parse_metrics_multiline_output(self, mock_docker):
        """Should preserve multiline user output."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()
        output = "line1\nline2\nline3\n__RLM_METRICS__:100:2097152\n"

        cleaned, cpu_ms, mem_bytes = repl._parse_metrics(output)

        assert cleaned == "line1\nline2\nline3\n"
        assert cpu_ms == 100
        assert mem_bytes == 2097152

    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    def test_parse_metrics_incomplete_trailer(self, mock_docker):
        """Should return None for incomplete trailer."""
        from rlm.repl.docker import DockerREPL

        repl = DockerREPL()
        output = "hello\n__RLM_METRICS__:42\n"

        cleaned, cpu_ms, mem_bytes = repl._parse_metrics(output)

        assert cleaned == "hello\n__RLM_METRICS__:42\n"
        assert cpu_ms is None
        assert mem_bytes is None


class TestDockerREPLResourceReporting:
    """Tests for resource reporting in execute()."""

    @pytest.mark.asyncio
    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    async def test_execute_populates_resource_metrics(self, mock_docker):
        """Should populate cpu_time_ms and memory_peak_bytes from container output."""
        from rlm.repl.docker import DockerREPL

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = b"hello\n__RLM_METRICS__:25:4194304\n"

        repl = DockerREPL()
        result = await repl.execute("print('hello')")

        assert result.output == "hello\n"
        assert result.error is None
        assert result.cpu_time_ms == 25
        assert result.memory_peak_bytes == 4194304

    @pytest.mark.asyncio
    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    async def test_execute_metrics_stripped_from_output(self, mock_docker):
        """Should strip metrics trailer from user-visible output."""
        from rlm.repl.docker import DockerREPL

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = b"result = 42\n__RLM_METRICS__:5:1024\n"

        repl = DockerREPL()
        result = await repl.execute("result = 42")

        assert "__RLM_METRICS__" not in result.output
        assert result.output == "result = 42\n"

    @pytest.mark.asyncio
    @patch("rlm.repl.docker.DOCKER_AVAILABLE", True)
    @patch("rlm.repl.docker.docker")
    async def test_execute_no_metrics_returns_none(self, mock_docker):
        """Should return None for metrics when trailer is absent."""
        from rlm.repl.docker import DockerREPL

        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = b"output only\n"

        repl = DockerREPL()
        result = await repl.execute("print('output only')")

        assert result.output == "output only\n"
        assert result.cpu_time_ms is None
        assert result.memory_peak_bytes is None
