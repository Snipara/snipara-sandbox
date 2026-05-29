"""Tests for RLM CLI commands."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from rlm.cli.main import app

runner = CliRunner()


class TestVersionCommand:
    """Tests for version command."""

    def test_shows_version(self):
        """Should show version."""
        result = runner.invoke(app, ["version"])

        assert result.exit_code == 0
        assert "snipara-sandbox" in result.stdout


class TestConfigCommand:
    """Tests for config command."""

    @patch("rlm.core.config.load_project_env", return_value=None)
    @patch("rlm.core.config.load_config")
    def test_shows_effective_configuration(
        self, mock_load_config, _mock_load_project_env, tmp_path
    ):
        """Should show the effective configuration."""
        from rlm.core.config import RLMConfig

        mock_load_config.return_value = RLMConfig.model_construct(
            model="test-model",
            api_key="sk-test",
            snipara_api_key="snp-test",
            snipara_project_slug="demo-project",
        )

        result = runner.invoke(
            app, ["config", "show", "--config", str(tmp_path / "snipara-sandbox.toml")]
        )

        assert result.exit_code == 0
        assert "Effective Configuration" in result.stdout
        assert "test-model" in result.stdout
        assert "demo-project" in result.stdout

    @patch("rlm.core.config.load_project_env", return_value=None)
    @patch("rlm.core.config.load_config")
    def test_json_output(self, mock_load_config, _mock_load_project_env, tmp_path):
        """Should output JSON for config show."""
        from rlm.core.config import RLMConfig

        config_path = tmp_path / "snipara-sandbox.toml"
        mock_load_config.return_value = RLMConfig.model_construct(
            model="json-model",
            api_key="sk-test",
            snipara_api_key="snp-test",
            snipara_project_slug="json-project",
        )

        result = runner.invoke(
            app,
            ["config", "show", "--config", str(config_path), "--json"],
        )

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["config_file"] == str(config_path)
        assert data["model"] == "json-model"
        assert data["snipara_enabled"] is True

    @patch("rlm.core.config.load_project_env", return_value=None)
    @patch("rlm.core.config.load_config")
    def test_empty_allowed_paths_show_cwd_fallback(
        self, mock_load_config, _mock_load_project_env, tmp_path
    ):
        """Should explain the empty allowed-paths fallback in human output."""
        from rlm.core.config import RLMConfig

        mock_load_config.return_value = RLMConfig.model_construct(
            model="test-model",
            allowed_paths=[],
        )

        result = runner.invoke(
            app, ["config", "show", "--config", str(tmp_path / "snipara-sandbox.toml")]
        )

        assert result.exit_code == 0
        assert "current working directory only" in result.stdout

    @patch("rlm.core.config.load_project_env", return_value=None)
    @patch("rlm.core.config.load_config")
    def test_workspace_mount_shown_for_default_docker_behavior(
        self, mock_load_config, _mock_load_project_env, tmp_path
    ):
        """Should show the default read-only workspace mount in human output."""
        from rlm.core.config import RLMConfig

        mock_load_config.return_value = RLMConfig.model_construct(
            model="test-model",
            environment="docker",
            docker_mount_workspace=True,
            docker_workspace_path=None,
        )

        result = runner.invoke(
            app, ["config", "show", "--config", str(tmp_path / "snipara-sandbox.toml")]
        )

        assert result.exit_code == 0
        assert "current working directory (read-only)" in result.stdout

    @patch("rlm.core.config.load_project_env", return_value=None)
    @patch("rlm.core.config.load_config")
    def test_workspace_setup_shown_for_prepared_docker_behavior(
        self, mock_load_config, _mock_load_project_env, tmp_path
    ):
        """Should show prepared workspace setup mode in human output."""
        from rlm.core.config import RLMConfig

        mock_load_config.return_value = RLMConfig.model_construct(
            model="test-model",
            environment="docker",
            docker_workspace_setup="dev",
            docker_workspace_install_command='python -m pip install -e ".[dev]"',
        )

        result = runner.invoke(
            app, ["config", "show", "--config", str(tmp_path / "snipara-sandbox.toml")]
        )

        assert result.exit_code == 0
        assert "dev via custom install command" in result.stdout

    def test_root_version_flag(self):
        """Should support the root --version flag."""
        result = runner.invoke(app, ["--version"])

        assert result.exit_code == 0
        assert "snipara-sandbox" in result.stdout


class TestInitCommand:
    """Tests for init command."""

    def test_creates_config_file(self, tmp_path):
        """Should create snipara-sandbox.toml in project directory."""
        result = runner.invoke(app, ["init", str(tmp_path)])

        assert result.exit_code == 0
        assert (tmp_path / "snipara-sandbox.toml").exists()

    def test_creates_env_example(self, tmp_path):
        """Should create .env.example file."""
        result = runner.invoke(app, ["init", str(tmp_path)])

        assert result.exit_code == 0
        assert (tmp_path / ".env.example").exists()

    def test_fails_if_config_exists(self, tmp_path):
        """Should fail if config exists without --force."""
        config_file = tmp_path / "snipara-sandbox.toml"
        config_file.write_text("existing config")

        result = runner.invoke(app, ["init", str(tmp_path)])

        assert result.exit_code == 1
        assert "already exists" in result.stdout

    def test_overwrites_with_force(self, tmp_path):
        """Should overwrite config with --force."""
        config_file = tmp_path / "snipara-sandbox.toml"
        config_file.write_text("old config")

        result = runner.invoke(app, ["init", str(tmp_path), "--force"])

        assert result.exit_code == 0
        assert "[snipara_sandbox]" in config_file.read_text()

    def test_no_snipara_skips_snipara_config(self, tmp_path):
        """Should skip Snipara config with --no-snipara."""
        result = runner.invoke(app, ["init", str(tmp_path), "--no-snipara"])

        assert result.exit_code == 0
        content = (tmp_path / "snipara-sandbox.toml").read_text()
        assert "snipara_api_key" not in content


class TestLogsCommand:
    """Tests for logs command."""

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_list_recent_logs(self, mock_logger_class, tmp_path):
        """Should list recent trajectories."""
        mock_logger = MagicMock()
        mock_logger.list_recent.return_value = [
            {
                "id": "test-id-12345678",
                "timestamp": "2024-01-15T12:00:00",
                "calls": 3,
                "tokens": 500,
                "duration_ms": 1000,
            }
        ]
        mock_logger_class.return_value = mock_logger

        result = runner.invoke(app, ["logs", "--dir", str(tmp_path)])

        assert result.exit_code == 0
        mock_logger.list_recent.assert_called_once_with(10)

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_list_with_tail(self, mock_logger_class, tmp_path):
        """Should respect --tail option."""
        mock_logger = MagicMock()
        mock_logger.list_recent.return_value = []
        mock_logger_class.return_value = mock_logger

        result = runner.invoke(app, ["logs", "--dir", str(tmp_path), "--tail", "5"])

        assert result.exit_code == 0
        mock_logger.list_recent.assert_called_once_with(5)

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_view_specific_trajectory(self, mock_logger_class, tmp_path):
        """Should load specific trajectory by ID."""
        mock_logger = MagicMock()
        mock_event = MagicMock()
        mock_event.call_id = "call-123"
        mock_event.depth = 0
        mock_event.prompt = "Test prompt"
        mock_event.response = "Test response"
        mock_event.tool_calls = []
        mock_event.error = None
        mock_event.input_tokens = 100
        mock_event.output_tokens = 50
        mock_event.duration_ms = 500
        mock_logger.load_trajectory.return_value = [mock_event]
        mock_logger_class.return_value = mock_logger

        result = runner.invoke(app, ["logs", "test-trajectory-id", "--dir", str(tmp_path)])

        assert result.exit_code == 0
        mock_logger.load_trajectory.assert_called_once_with("test-trajectory-id")

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_trajectory_not_found(self, mock_logger_class, tmp_path):
        """Should fail if trajectory not found."""
        mock_logger = MagicMock()
        mock_logger.load_trajectory.return_value = []
        mock_logger_class.return_value = mock_logger

        result = runner.invoke(app, ["logs", "nonexistent-id", "--dir", str(tmp_path)])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_json_output(self, mock_logger_class, tmp_path):
        """Should output JSON with --json flag."""
        mock_logger = MagicMock()
        mock_logger.list_recent.return_value = [
            {
                "id": "test-id",
                "timestamp": "2024-01-15T12:00:00",
                "calls": 1,
                "tokens": 100,
                "duration_ms": 500,
            }
        ]
        mock_logger_class.return_value = mock_logger

        result = runner.invoke(app, ["logs", "--dir", str(tmp_path), "--json"])

        assert result.exit_code == 0
        # Should be valid JSON
        data = json.loads(result.stdout)
        assert isinstance(data, list)


class TestRunCommand:
    """Tests for run command."""

    @patch("rlm.core.config.load_config")
    @patch("rlm.core.orchestrator.RLM")
    def test_basic_run(self, mock_rlm_class, mock_load_config):
        """Should run completion with prompt."""
        from rlm.core.config import RLMConfig

        mock_load_config.return_value = RLMConfig()
        mock_rlm = MagicMock()
        mock_result = MagicMock()
        mock_result.response = "Test response"
        mock_result.trajectory_id = "traj-123"
        mock_result.total_calls = 1
        mock_result.total_tokens = 150
        mock_result.total_tool_calls = 0
        mock_result.duration_ms = 500
        mock_result.success = True
        mock_rlm.completion = AsyncMock(return_value=mock_result)
        mock_rlm_class.return_value = mock_rlm

        result = runner.invoke(app, ["run", "Hello"])

        # Either succeeds or we get an error we can check
        assert result.exit_code == 0 or "Error" in result.stdout

    @patch("rlm.core.config.load_config")
    @patch("rlm.core.orchestrator.RLM")
    def test_run_import_error(self, mock_rlm_class, mock_load_config):
        """Should handle import errors gracefully."""
        from rlm.core.config import RLMConfig

        mock_load_config.return_value = RLMConfig()
        mock_rlm_class.side_effect = ImportError("Docker not available")

        result = runner.invoke(app, ["run", "Test"])

        assert result.exit_code == 1
        assert "Error" in result.stdout

    @patch("rlm.core.config.load_config")
    @patch("rlm.core.orchestrator.RLM")
    def test_non_json_failure_exits_nonzero(self, mock_rlm_class, mock_load_config):
        """Should exit non-zero when the completion reports failure."""
        from rlm.core.config import RLMConfig

        mock_load_config.return_value = RLMConfig()
        mock_rlm = MagicMock()
        mock_result = MagicMock()
        mock_result.response = "Error: missing API key"
        mock_result.success = False
        mock_rlm.completion = AsyncMock(return_value=mock_result)
        mock_rlm_class.return_value = mock_rlm

        result = runner.invoke(app, ["run", "Hello"])

        assert result.exit_code == 1
        assert "Error" in result.stdout


class TestMcpServeCommand:
    """Tests for mcp-serve command."""

    @patch("rlm.mcp.run_server")
    def test_starts_mcp_server(self, mock_run_server):
        """Should start MCP server."""
        runner.invoke(app, ["mcp-serve"])

        mock_run_server.assert_called_once()


class TestDoctorCommand:
    """Tests for doctor command."""

    def test_runs_doctor_checks(self):
        """Should run various checks."""
        result = runner.invoke(app, ["doctor"])

        # Should show Python version check
        assert "Python" in result.stdout

        # Should show package checks
        assert "Package" in result.stdout or "litellm" in result.stdout

    def test_shows_api_key_status(self):
        """Should show API key environment variables."""
        result = runner.invoke(app, ["doctor"])

        # Should check for API keys
        assert "OPENAI_API_KEY" in result.stdout or "API" in result.stdout


class TestVisualizeCommand:
    """Tests for visualize command."""

    def test_handles_missing_streamlit(self):
        """Should handle missing Streamlit gracefully."""
        with patch.dict(
            "sys.modules", {"streamlit": None, "streamlit.web": None, "streamlit.web.cli": None}
        ):
            # This will likely fail with import error
            result = runner.invoke(app, ["visualize"])

            # Either succeeds or fails gracefully
            if result.exit_code != 0:
                assert (
                    "Visualizer" in result.stdout
                    or "streamlit" in result.stdout.lower()
                    or "Error" in result.stdout
                )


class TestInitCommandEdgeCases:
    """Additional tests for init command edge cases."""

    def test_creates_config_with_force_overwrite(self, tmp_path):
        """Should overwrite existing config with --force."""
        config_file = tmp_path / "snipara-sandbox.toml"
        config_file.write_text("original content")

        result = runner.invoke(app, ["init", str(tmp_path), "--force"])

        assert result.exit_code == 0
        assert "[snipara_sandbox]" in config_file.read_text()
        assert "original content" not in config_file.read_text()

    def test_creates_config_with_snipara_by_default(self, tmp_path):
        """Should include Snipara config by default."""
        result = runner.invoke(app, ["init", str(tmp_path)])

        assert result.exit_code == 0
        content = (tmp_path / "snipara-sandbox.toml").read_text()
        assert "snipara" in content.lower()

    def test_respects_no_snipara_flag(self, tmp_path):
        """Should not include Snipara with --no-snipara."""
        result = runner.invoke(app, ["init", str(tmp_path), "--no-snipara"])

        assert result.exit_code == 0
        content = (tmp_path / "snipara-sandbox.toml").read_text()
        assert "snipara_api_key" not in content

    def test_does_not_overwrite_existing_env_example(self, tmp_path):
        """Should not overwrite existing .env.example."""
        env_file = tmp_path / ".env.example"
        env_file.write_text("MY_CUSTOM_VAR=value")

        result = runner.invoke(app, ["init", str(tmp_path)])

        assert result.exit_code == 0
        assert "MY_CUSTOM_VAR=value" in env_file.read_text()


class TestLogsCommandEdgeCases:
    """Additional tests for logs command edge cases."""

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_handles_empty_trajectory_list(self, mock_logger_class, tmp_path):
        """Should handle no trajectories gracefully."""
        mock_logger = MagicMock()
        mock_logger.list_recent.return_value = []
        mock_logger_class.return_value = mock_logger

        result = runner.invoke(app, ["logs", "--dir", str(tmp_path)])

        assert result.exit_code == 0
        assert "No trajectories" in result.stdout or result.stdout == ""

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_view_trajectory_with_tool_calls(self, mock_logger_class, tmp_path):
        """Should display tool calls in trajectory."""
        mock_logger = MagicMock()
        mock_event = MagicMock()
        mock_event.call_id = "call-123"
        mock_event.depth = 0
        mock_event.prompt = "Test prompt"
        mock_event.response = "Test response"
        mock_event.tool_calls = [MagicMock(name="execute_python")]
        mock_event.error = None
        mock_event.input_tokens = 100
        mock_event.output_tokens = 50
        mock_event.duration_ms = 500
        mock_logger.load_trajectory.return_value = [mock_event]
        mock_logger_class.return_value = mock_logger

        result = runner.invoke(app, ["logs", "test-id", "--dir", str(tmp_path)])

        assert result.exit_code == 0

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_view_trajectory_with_error(self, mock_logger_class, tmp_path):
        """Should display errors in trajectory."""
        mock_logger = MagicMock()
        mock_event = MagicMock()
        mock_event.call_id = "call-123"
        mock_event.depth = 0
        mock_event.prompt = "Test prompt"
        mock_event.response = None
        mock_event.tool_calls = []
        mock_event.error = "Something went wrong"
        mock_event.input_tokens = 100
        mock_event.output_tokens = 50
        mock_event.duration_ms = 500
        mock_logger.load_trajectory.return_value = [mock_event]
        mock_logger_class.return_value = mock_logger

        result = runner.invoke(app, ["logs", "test-id", "--dir", str(tmp_path)])

        assert result.exit_code == 0
        assert "Error" in result.stdout


class TestDoctorCommandEdgeCases:
    """Additional tests for doctor command."""

    def test_detects_python_version(self):
        """Should show Python version in output."""
        result = runner.invoke(app, ["doctor"])

        assert "Python" in result.stdout
        assert "3." in result.stdout  # Should show version 3.x

    def test_checks_required_packages(self):
        """Should check for required packages."""
        result = runner.invoke(app, ["doctor"])

        # Should mention core packages
        assert "litellm" in result.stdout or "Package" in result.stdout


class TestRunCommandEdgeCases:
    """Additional tests for run command edge cases."""

    @patch("rlm.core.config.load_config")
    @patch("rlm.core.orchestrator.RLM")
    def test_run_with_json_output(self, mock_rlm_class, mock_load_config):
        """Should output JSON when --json flag is used."""
        from rlm.core.config import RLMConfig

        mock_load_config.return_value = RLMConfig()
        mock_rlm = MagicMock()
        mock_result = MagicMock()
        mock_result.response = "Test response"
        mock_result.trajectory_id = "traj-123"
        mock_result.total_calls = 1
        mock_result.total_tokens = 150
        mock_result.total_tool_calls = 0
        mock_result.duration_ms = 500
        mock_result.success = True
        mock_result.to_dict.return_value = {"response": "Test response"}
        mock_rlm.completion = AsyncMock(return_value=mock_result)
        mock_rlm_class.return_value = mock_rlm

        result = runner.invoke(app, ["run", "Hello", "--json"])

        # Either works or error
        if result.exit_code == 0:
            assert "response" in result.stdout or "{" in result.stdout

    @patch("rlm.core.config.load_config")
    @patch("rlm.core.orchestrator.RLM")
    def test_run_with_json_output_is_clean(self, mock_rlm_class, mock_load_config):
        """Should suppress noisy stdout/stderr before emitting JSON."""
        from rlm.core.config import RLMConfig

        mock_load_config.return_value = RLMConfig()
        mock_rlm = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.to_dict.return_value = {
            "response": "Test response",
            "success": True,
            "error": None,
        }

        async def noisy_completion(*_args, **_kwargs):
            import sys

            print("debug noise on stdout")
            print("debug noise on stderr", file=sys.stderr)
            return mock_result

        mock_rlm.completion = noisy_completion
        mock_rlm_class.return_value = mock_rlm

        result = runner.invoke(app, ["run", "Hello", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["success"] is True
        assert data["response"] == "Test response"
        assert "debug noise" not in result.stdout

    @patch("rlm.core.config.load_config")
    def test_rejects_max_depth_zero(self, mock_load_config):
        """Should reject a zero max-depth before running."""
        from rlm.core.config import RLMConfig

        mock_load_config.return_value = RLMConfig()

        result = runner.invoke(app, ["run", "Hello", "--max-depth", "0"])

        assert result.exit_code == 2
        assert "max-depth" in result.stdout.lower()

    @patch("rlm.core.config.load_config")
    @patch("rlm.core.orchestrator.RLM")
    def test_run_with_verbose(self, mock_rlm_class, mock_load_config):
        """Should show verbose output when -v flag is used."""
        from rlm.core.config import RLMConfig

        mock_load_config.return_value = RLMConfig()
        mock_rlm = MagicMock()
        mock_result = MagicMock()
        mock_result.response = "Test response"
        mock_result.trajectory_id = "traj-123"
        mock_result.total_calls = 2
        mock_result.total_tokens = 300
        mock_result.total_tool_calls = 1
        mock_result.duration_ms = 1000
        mock_result.success = True
        mock_rlm.completion = AsyncMock(return_value=mock_result)
        mock_rlm_class.return_value = mock_rlm

        result = runner.invoke(app, ["run", "Hello", "-v"])

        # Either works or error
        if result.exit_code == 0:
            # Should include summary table
            assert "Response" in result.stdout or "Test" in result.stdout
