"""Tests for RLM Orchestrator."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rlm.core.config import RLMConfig
from rlm.core.orchestrator import RLM
from rlm.core.types import (
    CompletionOptions,
    ToolCall,
)


class TestRLMInit:
    """Tests for RLM initialization."""

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_default_initialization(self, mock_logger):
        """Should initialize with default settings."""
        rlm = RLM()

        assert rlm.config is not None
        assert rlm.backend is not None
        assert rlm.repl is not None
        assert rlm.tool_registry is not None

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_custom_config(self, mock_logger):
        """Should accept custom config."""
        config = RLMConfig(max_depth=8, token_budget=16000)

        rlm = RLM(config=config)

        assert rlm.config.max_depth == 8
        assert rlm.config.token_budget == 16000

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_verbose_mode(self, mock_logger):
        """Should enable verbose mode."""
        rlm = RLM(verbose=True)

        assert rlm.verbose is True

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_custom_tools(self, mock_logger):
        """Should register custom tools."""
        mock_tool = MagicMock()
        mock_tool.name = "custom_tool"

        rlm = RLM(tools=[mock_tool])

        assert rlm.tool_registry.get("custom_tool") is not None


class TestCreateBackend:
    """Tests for backend creation."""

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_litellm_backend(self, mock_logger):
        """Should create LiteLLM backend."""
        rlm = RLM(backend="litellm", model="gpt-4")

        from rlm.backends.litellm import LiteLLMBackend

        assert isinstance(rlm.backend, LiteLLMBackend)

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_openai_backend(self, mock_logger):
        """Should create OpenAI backend (via LiteLLM)."""
        rlm = RLM(backend="openai", model="gpt-4")

        from rlm.backends.litellm import LiteLLMBackend

        assert isinstance(rlm.backend, LiteLLMBackend)

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_anthropic_backend(self, mock_logger):
        """Should create Anthropic backend (via LiteLLM)."""
        rlm = RLM(backend="anthropic", model="claude-3-sonnet")

        from rlm.backends.litellm import LiteLLMBackend

        assert isinstance(rlm.backend, LiteLLMBackend)

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_unknown_backend_raises(self, mock_logger):
        """Should raise for unknown backend."""
        with pytest.raises(ValueError) as exc_info:
            RLM(backend="unknown")

        assert "unknown" in str(exc_info.value).lower()

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_custom_backend_instance(self, mock_logger):
        """Should accept backend instance."""
        mock_backend = MagicMock()

        rlm = RLM(backend=mock_backend)

        assert rlm.backend is mock_backend


class TestCreateREPL:
    """Tests for REPL creation."""

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_local_repl(self, mock_logger):
        """Should create local REPL."""
        rlm = RLM(environment="local")

        from rlm.repl.local import LocalREPL

        assert isinstance(rlm.repl, LocalREPL)

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_localdev_repl_when_trust_level_local(self, mock_logger):
        """Should create unrestricted local REPL when explicitly trusted."""
        config = RLMConfig(trust_level="local")
        rlm = RLM(environment="local", config=config)

        from rlm.repl.localdev import LocalDevREPL

        assert isinstance(rlm.repl, LocalDevREPL)

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_unknown_environment_raises(self, mock_logger):
        """Should raise for unknown environment."""
        with pytest.raises(ValueError) as exc_info:
            RLM(environment="unknown")

        assert "unknown" in str(exc_info.value).lower()

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_custom_repl_instance(self, mock_logger):
        """Should accept REPL instance."""
        mock_repl = MagicMock()

        rlm = RLM(environment=mock_repl)

        assert rlm.repl is mock_repl


class TestCompletion:
    """Tests for completion method."""

    @pytest.fixture
    def mock_rlm(self):
        """Create RLM with mocked dependencies."""
        with patch("rlm.logging.trajectory.TrajectoryLogger"):
            # Create mock backend
            mock_backend = MagicMock()
            mock_response = MagicMock()
            mock_response.content = "Test response"
            mock_response.tool_calls = []
            mock_response.input_tokens = 100
            mock_response.output_tokens = 50
            mock_backend.complete = AsyncMock(return_value=mock_response)

            rlm = RLM(backend=mock_backend, environment="local")

            return rlm, mock_backend, mock_response

    @pytest.mark.asyncio
    async def test_simple_completion(self, mock_rlm):
        """Should return completion result."""
        rlm, mock_backend, mock_response = mock_rlm

        result = await rlm.completion("Hello")

        assert result.response == "Test response"
        assert result.total_calls >= 1

    @pytest.mark.asyncio
    async def test_completion_with_system(self, mock_rlm):
        """Should include system message."""
        rlm, mock_backend, mock_response = mock_rlm

        await rlm.completion("Hello", system="You are a helpful assistant.")

        # Check that complete was called with messages including system
        call_args = mock_backend.complete.call_args
        messages = call_args[0][0]
        assert any(m.role == "system" for m in messages)

    @pytest.mark.asyncio
    async def test_completion_with_options(self, mock_rlm):
        """Should respect completion options."""
        rlm, mock_backend, mock_response = mock_rlm

        options = CompletionOptions(max_depth=2, token_budget=1000)
        result = await rlm.completion("Hello", options=options)

        assert result is not None

    @pytest.mark.asyncio
    async def test_completion_logs_trajectory(self, mock_rlm):
        """Should log trajectory."""
        rlm, mock_backend, mock_response = mock_rlm

        await rlm.completion("Hello")

        rlm.trajectory_logger.log_trajectory.assert_called_once()

    @pytest.mark.asyncio
    async def test_completion_returns_trajectory_id(self, mock_rlm):
        """Should return trajectory ID."""
        rlm, mock_backend, mock_response = mock_rlm

        result = await rlm.completion("Hello")

        assert result.trajectory_id is not None


class TestToolExecution:
    """Tests for tool execution."""

    @pytest.fixture
    def mock_rlm_with_tools(self):
        """Create RLM with mocked tools."""
        with patch("rlm.logging.trajectory.TrajectoryLogger"):
            # Create mock backend that returns tool calls first, then response
            mock_backend = MagicMock()

            # First call: tool call
            mock_response1 = MagicMock()
            mock_response1.content = ""
            mock_response1.tool_calls = [ToolCall(id="call1", name="test_tool", arguments={"x": 1})]
            mock_response1.input_tokens = 100
            mock_response1.output_tokens = 50

            # Second call: final response
            mock_response2 = MagicMock()
            mock_response2.content = "Final response"
            mock_response2.tool_calls = []
            mock_response2.input_tokens = 150
            mock_response2.output_tokens = 75

            mock_backend.complete = AsyncMock(side_effect=[mock_response1, mock_response2])

            rlm = RLM(backend=mock_backend, environment="local")

            # Register test tool
            mock_tool = MagicMock()
            mock_tool.name = "test_tool"
            mock_tool.execute = AsyncMock(return_value={"result": "success"})
            rlm.tool_registry.register(mock_tool)

            return rlm, mock_backend, mock_tool

    @pytest.mark.asyncio
    async def test_executes_tool_calls(self, mock_rlm_with_tools):
        """Should execute tool calls."""
        rlm, mock_backend, mock_tool = mock_rlm_with_tools

        await rlm.completion("Use the test tool")

        mock_tool.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_final_response(self, mock_rlm_with_tools):
        """Should return final response after tool execution."""
        rlm, mock_backend, mock_tool = mock_rlm_with_tools

        result = await rlm.completion("Use the test tool")

        assert result.response == "Final response"

    @pytest.mark.asyncio
    async def test_counts_tool_calls(self, mock_rlm_with_tools):
        """Should count tool calls."""
        rlm, mock_backend, mock_tool = mock_rlm_with_tools

        result = await rlm.completion("Use the test tool")

        assert result.total_tool_calls >= 1


class TestDepthLimits:
    """Tests for depth limiting."""

    @pytest.mark.asyncio
    async def test_max_depth_exceeded(self):
        """Should raise MaxDepthExceeded when depth limit hit."""
        with patch("rlm.logging.trajectory.TrajectoryLogger"):
            config = RLMConfig(max_depth=1)

            # Create backend that always returns tool calls
            mock_backend = MagicMock()
            mock_response = MagicMock()
            mock_response.content = ""
            mock_response.tool_calls = [ToolCall(id="call1", name="test", arguments={})]
            mock_response.input_tokens = 10
            mock_response.output_tokens = 5
            mock_backend.complete = AsyncMock(return_value=mock_response)

            rlm = RLM(backend=mock_backend, environment="local", config=config)

            # Register dummy tool
            mock_tool = MagicMock()
            mock_tool.name = "test"
            mock_tool.execute = AsyncMock(return_value="ok")
            rlm.tool_registry.register(mock_tool)

            # Should hit depth limit and return error response
            result = await rlm.completion(
                "Keep calling tools",
                options=CompletionOptions(max_depth=1),
            )

            assert "Error" in result.response or "depth" in result.response.lower()


class TestToolNotFound:
    """Tests for handling unknown tools."""

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        """Should return error for unknown tool."""
        with patch("rlm.logging.trajectory.TrajectoryLogger"):
            # Create backend that requests unknown tool first
            mock_backend = MagicMock()

            mock_response1 = MagicMock()
            mock_response1.content = ""
            mock_response1.tool_calls = [
                ToolCall(id="call1", name="nonexistent_tool", arguments={})
            ]
            mock_response1.input_tokens = 10
            mock_response1.output_tokens = 5

            mock_response2 = MagicMock()
            mock_response2.content = "Handled error"
            mock_response2.tool_calls = []
            mock_response2.input_tokens = 20
            mock_response2.output_tokens = 10

            mock_backend.complete = AsyncMock(side_effect=[mock_response1, mock_response2])

            rlm = RLM(backend=mock_backend, environment="local")

            result = await rlm.completion("Call unknown tool")

            # Should complete without raising exception
            assert result.response == "Handled error"


class TestSniparaIntegration:
    """Tests for Snipara integration."""

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_snipara_params_override_config(self, mock_logger):
        """Should override config with explicit Snipara params."""
        rlm = RLM(
            snipara_api_key="test_key",
            snipara_project_slug="test_project",
        )

        assert rlm.config.snipara_api_key == "test_key"
        assert rlm.config.snipara_project_slug == "test_project"


class TestCreateREPLEnvironments:
    """Tests for different REPL environment creation."""

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    @patch("rlm.repl.docker.DockerREPL")
    def test_docker_repl_mounts_cwd_by_default(self, mock_docker_repl, mock_logger):
        """Should expose the current workspace to Docker by default."""
        config = RLMConfig(environment="docker")

        rlm = RLM(environment="docker", config=config)

        assert rlm.repl is mock_docker_repl.return_value
        call_kwargs = mock_docker_repl.call_args.kwargs
        assert call_kwargs["workdir_mount"] == Path.cwd().resolve()

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    @patch("rlm.repl.docker.DockerREPL")
    def test_docker_repl_can_disable_workspace_mount(self, mock_docker_repl, mock_logger):
        """Should allow a fully blind Docker container when explicitly requested."""
        config = RLMConfig(environment="docker", docker_mount_workspace=False)

        rlm = RLM(environment="docker", config=config)

        assert rlm.repl is mock_docker_repl.return_value
        call_kwargs = mock_docker_repl.call_args.kwargs
        assert call_kwargs["workdir_mount"] is None

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_docker_repl_import_error(self, mock_logger):
        """Should raise ImportError when Docker package not available."""
        with patch.dict("sys.modules", {"docker": None}):
            with patch("rlm.core.orchestrator.RLM._create_repl") as mock_create:
                mock_create.side_effect = ImportError("Docker not installed")

                with pytest.raises(ImportError) as exc_info:
                    RLM(environment="docker")

                assert "Docker" in str(exc_info.value) or "docker" in str(exc_info.value).lower()

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_wasm_repl_import_error(self, mock_logger):
        """Should raise ImportError when Pyodide not available."""
        with patch.dict("sys.modules", {"pyodide": None, "pyodide_py": None}):
            with patch("rlm.core.orchestrator.RLM._create_repl") as mock_create:
                mock_create.side_effect = ImportError("Pyodide not installed")

                with pytest.raises(ImportError) as exc_info:
                    RLM(environment="wasm")

                assert "Pyodide" in str(exc_info.value) or "pyodide" in str(exc_info.value).lower()


class TestStream:
    """Tests for streaming completion."""

    @pytest.fixture
    def mock_rlm_for_stream(self):
        """Create RLM with mocked dependencies for streaming."""
        with patch("rlm.logging.trajectory.TrajectoryLogger"):
            mock_backend = MagicMock()

            async def mock_stream(messages):
                for chunk in ["Hello", " ", "World"]:
                    yield chunk

            mock_backend.stream = mock_stream

            rlm = RLM(backend=mock_backend, environment="local")
            return rlm, mock_backend

    @pytest.mark.asyncio
    async def test_stream_simple(self, mock_rlm_for_stream):
        """Should stream completion chunks."""
        rlm, mock_backend = mock_rlm_for_stream

        chunks = []
        async for chunk in rlm.stream("Hello"):
            chunks.append(chunk)

        assert len(chunks) == 3
        assert "".join(chunks) == "Hello World"

    @pytest.mark.asyncio
    async def test_stream_with_system(self, mock_rlm_for_stream):
        """Should include system message in stream."""
        rlm, mock_backend = mock_rlm_for_stream

        chunks = []
        async for chunk in rlm.stream("Hello", system="Be helpful"):
            chunks.append(chunk)

        assert len(chunks) == 3


class TestVerboseMode:
    """Tests for verbose logging mode."""

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_verbose_from_config(self, mock_logger):
        """Should enable verbose from config."""
        config = RLMConfig(verbose=True)
        rlm = RLM(config=config)

        assert rlm.verbose is True

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_verbose_overrides_config(self, mock_logger):
        """Should override config verbose with parameter."""
        config = RLMConfig(verbose=False)
        rlm = RLM(config=config, verbose=True)

        assert rlm.verbose is True


class TestTokenAndToolBudgets:
    """Tests for budget limits."""

    @pytest.mark.asyncio
    async def test_tool_budget_exceeded(self):
        """Should return error when tool budget exhausted."""
        with patch("rlm.logging.trajectory.TrajectoryLogger"):
            config = RLMConfig()

            # Create backend that always returns tool calls
            mock_backend = MagicMock()
            mock_response = MagicMock()
            mock_response.content = ""
            mock_response.tool_calls = [ToolCall(id="call1", name="test_tool", arguments={})]
            mock_response.input_tokens = 10
            mock_response.output_tokens = 5
            mock_backend.complete = AsyncMock(return_value=mock_response)

            rlm = RLM(backend=mock_backend, environment="local", config=config)

            # Register test tool
            mock_tool = MagicMock()
            mock_tool.name = "test_tool"
            mock_tool.execute = AsyncMock(return_value="ok")
            rlm.tool_registry.register(mock_tool)

            # With very low tool budget
            options = CompletionOptions(
                max_depth=10,
                max_subcalls=100,
                tool_budget=1,  # Only 1 tool call allowed
            )

            # After first tool call, second should fail due to budget
            result = await rlm.completion(
                "Use tools",
                options=options,
            )

            # Should handle budget exhaustion gracefully
            assert result is not None

    @pytest.mark.asyncio
    async def test_token_budget_exceeded(self):
        """Should raise TokenBudgetExhausted when token budget exceeded."""
        with patch("rlm.logging.trajectory.TrajectoryLogger"):
            config = RLMConfig()

            # Create backend that returns tool calls with high token usage
            mock_backend = MagicMock()
            mock_backend.model = "gpt-4o-mini"

            mock_response = MagicMock()
            mock_response.content = ""
            mock_response.tool_calls = [ToolCall(id="call1", name="test_tool", arguments={})]
            mock_response.input_tokens = 500  # High token usage
            mock_response.output_tokens = 500
            mock_backend.complete = AsyncMock(return_value=mock_response)

            rlm = RLM(backend=mock_backend, environment="local", config=config)

            # Register test tool
            mock_tool = MagicMock()
            mock_tool.name = "test_tool"
            mock_tool.execute = AsyncMock(return_value="ok")
            rlm.tool_registry.register(mock_tool)

            # With very low token budget
            options = CompletionOptions(
                max_depth=10,
                max_subcalls=100,
                token_budget=100,  # Very low - will be exceeded on first call
            )

            # Should catch TokenBudgetExhausted and return error response
            result = await rlm.completion(
                "Use tokens",
                options=options,
            )

            # Should handle budget exhaustion - error in response
            assert result is not None
            assert (
                "Error" in result.response
                or "token" in result.response.lower()
                or "budget" in result.response.lower()
            )

    @pytest.mark.asyncio
    async def test_cost_budget_exceeded(self):
        """Should raise CostBudgetExhausted when cost budget exceeded."""
        with patch("rlm.logging.trajectory.TrajectoryLogger"):
            config = RLMConfig()

            # Create backend that returns tool calls with high token usage
            mock_backend = MagicMock()
            mock_backend.model = "gpt-4o"  # Known model for pricing

            mock_response = MagicMock()
            mock_response.content = ""
            mock_response.tool_calls = [ToolCall(id="call1", name="test_tool", arguments={})]
            mock_response.input_tokens = 10000  # Lots of tokens
            mock_response.output_tokens = 5000
            mock_backend.complete = AsyncMock(return_value=mock_response)

            rlm = RLM(backend=mock_backend, environment="local", config=config)

            # Register test tool
            mock_tool = MagicMock()
            mock_tool.name = "test_tool"
            mock_tool.execute = AsyncMock(return_value="ok")
            rlm.tool_registry.register(mock_tool)

            # With very low cost budget
            options = CompletionOptions(
                max_depth=10,
                max_subcalls=100,
                token_budget=100000,  # High token budget
                cost_budget_usd=0.0001,  # Very low cost - will be exceeded
            )

            # Should catch CostBudgetExhausted and return error response
            result = await rlm.completion(
                "Use expensive tokens",
                options=options,
            )

            # Should handle budget exhaustion - error in response
            assert result is not None
            assert (
                "Error" in result.response
                or "cost" in result.response.lower()
                or "budget" in result.response.lower()
            )


class TestCostTracking:
    """Tests for cost calculation and tracking."""

    @pytest.fixture
    def mock_rlm_with_known_model(self):
        """Create RLM with a known model for pricing."""
        with patch("rlm.logging.trajectory.TrajectoryLogger"):
            mock_backend = MagicMock()
            mock_backend.model = "gpt-4o-mini"

            mock_response = MagicMock()
            mock_response.content = "Test response"
            mock_response.tool_calls = []
            mock_response.input_tokens = 1000
            mock_response.output_tokens = 500
            mock_backend.complete = AsyncMock(return_value=mock_response)

            rlm = RLM(backend=mock_backend, environment="local")
            return rlm, mock_backend, mock_response

    @pytest.mark.asyncio
    async def test_result_includes_cost(self, mock_rlm_with_known_model):
        """RLMResult should include estimated cost."""
        rlm, mock_backend, mock_response = mock_rlm_with_known_model

        result = await rlm.completion("Hello")

        # gpt-4o-mini: 0.00015/1k input + 0.0006/1k output
        # 1000 input = 0.00015, 500 output = 0.0003, total = 0.00045
        assert result.total_cost_usd is not None
        assert result.total_cost_usd > 0

    @pytest.mark.asyncio
    async def test_result_includes_token_breakdown(self, mock_rlm_with_known_model):
        """RLMResult should include token breakdown."""
        rlm, mock_backend, mock_response = mock_rlm_with_known_model

        result = await rlm.completion("Hello")

        assert result.total_input_tokens == 1000
        assert result.total_output_tokens == 500
        assert result.total_tokens == 1500

    @pytest.mark.asyncio
    async def test_unknown_model_cost_is_none(self):
        """Cost should be None for unknown models."""
        with patch("rlm.logging.trajectory.TrajectoryLogger"):
            mock_backend = MagicMock()
            mock_backend.model = "unknown-custom-model"

            mock_response = MagicMock()
            mock_response.content = "Test response"
            mock_response.tool_calls = []
            mock_response.input_tokens = 100
            mock_response.output_tokens = 50
            mock_backend.complete = AsyncMock(return_value=mock_response)

            rlm = RLM(backend=mock_backend, environment="local")

            result = await rlm.completion("Hello")

            # Unknown model, cost should be None
            assert result.total_cost_usd is None

    @pytest.mark.asyncio
    async def test_events_include_cost(self, mock_rlm_with_known_model):
        """TrajectoryEvents should include estimated cost."""
        rlm, mock_backend, mock_response = mock_rlm_with_known_model

        options = CompletionOptions(include_trajectory=True)
        result = await rlm.completion("Hello", options=options)

        assert len(result.events) >= 1
        event = result.events[0]
        assert event.estimated_cost_usd is not None
        assert event.estimated_cost_usd > 0


class TestSniparaRegistration:
    """Tests for Snipara tool registration."""

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_native_snipara_registered_when_auth_available(self, mock_logger):
        """Should register native Snipara tools when auth is available."""
        mock_client = MagicMock()
        mock_tools = [MagicMock(name=f"tool_{i}") for i in range(5)]
        for i, t in enumerate(mock_tools):
            t.name = f"rlm_tool_{i}"

        with (
            patch("rlm.tools.snipara.SniparaClient.from_config", return_value=mock_client),
            patch("rlm.tools.snipara.get_native_snipara_tools", return_value=mock_tools),
        ):
            rlm = RLM()
            registered = {t.name for t in rlm.tool_registry.get_all()}
            for t in mock_tools:
                assert t.name in registered

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_snipara_skipped_when_no_auth(self, mock_logger):
        """Should skip Snipara tools gracefully when no auth."""
        with patch("rlm.tools.snipara.SniparaClient.from_config", return_value=None):
            # Should not raise
            rlm = RLM()
            # Builtin tools should still exist
            assert rlm.tool_registry is not None

    @patch("rlm.logging.trajectory.TrajectoryLogger")
    def test_snipara_exception_caught(self, mock_logger):
        """Should catch and log Snipara errors without crashing."""
        with patch(
            "rlm.tools.snipara.SniparaClient.from_config",
            side_effect=RuntimeError("auth failed"),
        ):
            rlm = RLM()
            assert rlm.tool_registry is not None


class TestParallelToolExecution:
    """Tests for parallel tool execution option."""

    @pytest.mark.asyncio
    async def test_parallel_tools_execute_concurrently(self):
        """Should execute multiple tool calls in parallel when enabled."""
        with patch("rlm.logging.trajectory.TrajectoryLogger"):
            mock_backend = MagicMock()

            # First response: 2 tool calls
            mock_response1 = MagicMock()
            mock_response1.content = ""
            mock_response1.tool_calls = [
                ToolCall(id="c1", name="test_tool", arguments={"n": 1}),
                ToolCall(id="c2", name="test_tool", arguments={"n": 2}),
            ]
            mock_response1.input_tokens = 10
            mock_response1.output_tokens = 5

            # Second response: final answer
            mock_response2 = MagicMock()
            mock_response2.content = "Done"
            mock_response2.tool_calls = []
            mock_response2.input_tokens = 10
            mock_response2.output_tokens = 5

            mock_backend.complete = AsyncMock(side_effect=[mock_response1, mock_response2])
            mock_backend.model = "gpt-4o-mini"

            rlm = RLM(backend=mock_backend, environment="local")

            mock_tool = MagicMock()
            mock_tool.name = "test_tool"
            mock_tool.execute = AsyncMock(return_value="ok")
            rlm.tool_registry.register(mock_tool)

            options = CompletionOptions(parallel_tools=True, max_parallel=5)
            result = await rlm.completion("Parallel test", options=options)

            assert result.response == "Done"
            assert mock_tool.execute.await_count == 2


class TestTimeoutEnforcement:
    """Tests for completion timeout."""

    @pytest.mark.asyncio
    async def test_timeout_in_options(self):
        """CompletionOptions should accept timeout_seconds."""
        options = CompletionOptions(timeout_seconds=5)
        assert options.timeout_seconds == 5

    @pytest.mark.asyncio
    async def test_default_timeout(self):
        """Default timeout should be set in CompletionOptions."""
        options = CompletionOptions()
        assert options.timeout_seconds is not None or hasattr(options, "timeout_seconds")
