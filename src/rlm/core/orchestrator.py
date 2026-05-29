"""Snipara Sandbox orchestrator - the main entry point for recursive completions."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog

from rlm.core.config import RLMConfig, load_config
from rlm.core.exceptions import (
    CostBudgetExhausted,
    MaxDepthExceeded,
    TimeoutExceeded,
    TokenBudgetExhausted,
    ToolExecutionError,
    ToolNotFoundError,
)
from rlm.core.pricing import estimate_cost
from rlm.core.types import (
    CompletionOptions,
    Message,
    RLMResult,
    StreamOptions,
    ToolCall,
    ToolResult,
    TrajectoryEvent,
)

if TYPE_CHECKING:
    from rlm.backends.base import BaseBackend, Tool
    from rlm.repl.base import BaseREPL

logger = structlog.get_logger()


class RLM:
    """Snipara Sandbox runtime.

    The main entry point for recursive LLM completions with tool use
    and sandboxed code execution.

    Example:
        ```python
        from snipara_sandbox import SniparaSandbox

        sandbox = SniparaSandbox(model="gpt-4o-mini", environment="docker")
        result = await sandbox.completion("Analyze data.csv")
        print(result.response)
        ```
    """

    def __init__(
        self,
        backend: str | BaseBackend = "litellm",
        model: str = "gpt-4o-mini",
        environment: str | BaseREPL = "local",
        config: RLMConfig | None = None,
        tools: list[Tool] | None = None,
        verbose: bool = False,
        snipara_api_key: str | None = None,
        snipara_project_slug: str | None = None,
    ):
        """Initialize the Snipara Sandbox runtime.

        Args:
            backend: LLM backend ("litellm", "openai", "anthropic") or instance
            model: Model name to use (e.g., "gpt-4o-mini", "claude-3-sonnet")
            environment: REPL environment ("local", "docker") or instance
            config: Optional RLMConfig instance
            tools: Optional list of custom tools to register
            verbose: Enable verbose logging
            snipara_api_key: Snipara API key (or set SNIPARA_API_KEY env var)
            snipara_project_slug: Snipara project slug (or set SNIPARA_PROJECT_SLUG)
        """
        self.config = config or load_config()
        self.verbose = verbose or self.config.verbose

        # Override config with explicit parameters
        if snipara_api_key:
            self.config.snipara_api_key = snipara_api_key
        if snipara_project_slug:
            self.config.snipara_project_slug = snipara_project_slug

        # Setup backend
        if isinstance(backend, str):
            self.backend = self._create_backend(backend, model)
        else:
            self.backend = backend

        # Setup REPL
        if isinstance(environment, str):
            self.repl = self._create_repl(environment)
        else:
            self.repl = environment

        # Setup tools
        from rlm.tools.registry import ToolRegistry

        self.tool_registry = ToolRegistry()

        # Register builtin tools
        self._register_builtin_tools()

        # Register custom tools
        if tools:
            for tool in tools:
                self.tool_registry.register(tool)

        # Auto-register Snipara tools if available
        self._register_snipara_tools()

        # Setup logging
        from rlm.logging.trajectory import TrajectoryLogger

        self.trajectory_logger = TrajectoryLogger(
            log_dir=self.config.log_dir,
            verbose=self.verbose,
        )

        if self.verbose:
            logger.info(
                "Snipara Sandbox initialized",
                backend=backend if isinstance(backend, str) else type(backend).__name__,
                model=model,
                environment=environment
                if isinstance(environment, str)
                else type(environment).__name__,
                tools_count=len(self.tool_registry),
                snipara_enabled=self.config.snipara_enabled,
            )

    def _create_backend(self, backend: str, model: str) -> BaseBackend:
        """Create backend from string identifier."""
        from rlm.backends.litellm import LiteLLMBackend

        if backend == "litellm":
            return LiteLLMBackend(model=model, temperature=self.config.temperature)
        if backend == "openai":
            return LiteLLMBackend(model=model, temperature=self.config.temperature)
        if backend == "anthropic":
            return LiteLLMBackend(model=model, temperature=self.config.temperature)

        raise ValueError(f"Unknown backend: {backend}. Supported: litellm, openai, anthropic")

    def _create_repl(self, environment: str) -> BaseREPL:
        """Create REPL from string identifier."""
        from rlm.repl.local import LocalREPL
        from rlm.repl.localdev import LocalDevREPL

        if environment == "local":
            if self.config.trust_level == "local":
                return LocalDevREPL(timeout=self.config.docker_timeout, audit_log=True)
            return LocalREPL(timeout=self.config.docker_timeout)

        if environment == "docker":
            try:
                from rlm.repl.docker import DockerREPL

                return DockerREPL(
                    image=self._resolve_docker_image(),
                    cpus=self.config.docker_cpus,
                    memory=self.config.docker_memory,
                    timeout=self.config.docker_timeout,
                    network_disabled=self.config.docker_network_disabled,
                    workdir_mount=self._resolve_docker_workdir_mount(),
                )
            except ImportError:
                raise ImportError(
                    "Docker support requires 'docker' package. "
                    "Install with: pip install snipara-sandbox[docker]"
                ) from None

        if environment == "wasm":
            try:
                from rlm.repl.wasm import WasmREPL

                return WasmREPL(timeout=self.config.docker_timeout)
            except ImportError:
                raise ImportError(
                    "WebAssembly support requires 'pyodide' package. "
                    "Install with: pip install pyodide-py"
                ) from None

        raise ValueError(f"Unknown environment: {environment}. Supported: local, docker, wasm")

    def _resolve_docker_workdir_mount(self) -> Path | None:
        """Resolve the host workspace path exposed read-only to Docker executions."""
        if not self.config.docker_mount_workspace:
            return None

        mount_path = self.config.docker_workspace_path or Path.cwd()
        return mount_path.resolve()

    def _resolve_docker_image(self) -> str:
        """Resolve the Docker image, optionally preparing a workspace-specific one."""
        setup_mode = self.config.docker_workspace_setup
        if setup_mode == "none":
            return self.config.docker_image

        from rlm.repl.docker import build_workspace_image

        workspace_path = self.config.docker_workspace_path or Path.cwd()
        return build_workspace_image(
            base_image=self.config.docker_image,
            workspace_path=workspace_path,
            setup_mode=setup_mode,
            install_command=self.config.docker_workspace_install_command,
        )

    def _register_builtin_tools(self) -> None:
        """Register builtin tools."""
        from rlm.tools.builtin import get_builtin_tools

        for tool in get_builtin_tools(self.repl, self.config.allowed_paths):
            self.tool_registry.register(tool)

    def _register_snipara_tools(self) -> None:
        """Register Snipara tools, preferring native HTTP client over snipara-mcp.

        Resolution order:
        1. Native tools via SniparaClient (uses OAuth or API key via auth.py)
        2. snipara-mcp package import (backward compatibility for API key users)
        3. Skip with debug log

        Memory tools are auto-enabled when OAuth tokens are available.
        """
        # Check if we should auto-enable memory based on OAuth status
        memory_enabled = self.config.memory_enabled
        if not memory_enabled:
            try:
                from rlm.mcp.auth import get_auth_status

                auth_status = get_auth_status()
                if auth_status.get("auth_method") == "oauth":
                    memory_enabled = True
                    logger.info(
                        "Auto-enabling memory tools (OAuth detected)",
                        projects=len(auth_status.get("oauth_projects", [])),
                    )
            except Exception:
                pass  # Ignore auth check failures

        # --- Attempt 1: Native tools via SniparaClient ---
        try:
            from rlm.tools.snipara import SniparaClient, get_native_snipara_tools

            client = SniparaClient.from_config(self.config)
            if client is not None:
                tools = get_native_snipara_tools(
                    client=client,
                    memory_enabled=memory_enabled,
                )
                for tool in tools:
                    self.tool_registry.register(tool)
                logger.info(
                    "Snipara tools registered (native HTTP)",
                    count=len(tools),
                    memory_enabled=memory_enabled,
                )
                return
        except Exception as e:
            logger.debug(
                "Native Snipara tools unavailable, trying snipara-mcp fallback",
                error=str(e),
            )

        # --- Attempt 2: snipara-mcp package (backward compatibility) ---
        if not self.config.snipara_enabled:
            logger.debug("Snipara not configured, skipping tool registration")
            return

        memory_tool_names = {"rlm_remember", "rlm_recall", "rlm_memories", "rlm_forget"}

        try:
            from snipara_mcp.rlm_tools import get_snipara_tools

            tools = get_snipara_tools(
                api_key=self.config.snipara_api_key,
                project_slug=self.config.snipara_project_slug,
            )
            registered = 0
            for tool in tools:
                if tool.name in memory_tool_names and not memory_enabled:
                    continue
                self.tool_registry.register(tool)
                registered += 1
            logger.info(
                "Snipara tools registered (snipara-mcp)",
                count=registered,
                memory_enabled=memory_enabled,
            )
        except ImportError:
            logger.debug(
                "snipara-mcp not installed, skipping Snipara tools. "
                "Install with: pip install snipara-sandbox[snipara]"
            )

    async def completion(
        self,
        prompt: str,
        system: str | None = None,
        options: CompletionOptions | None = None,
    ) -> RLMResult:
        """Execute a recursive completion.

        The LLM can call tools, execute code in the REPL, and spawn
        sub-completions up to the configured depth limit.

        Args:
            prompt: The user's prompt/question
            system: Optional system message for context
            options: Completion options (limits, budgets, etc.)

        Returns:
            RLMResult with the response and execution trajectory

        Example:
            ```python
            result = await rlm.completion(
                "Find all TODO comments in the codebase",
                system="You are a code analyst.",
                options=CompletionOptions(max_depth=3),
            )
            print(result.response)
            ```
        """
        options = options or CompletionOptions(
            max_depth=self.config.max_depth,
            max_subcalls=self.config.max_subcalls,
            token_budget=self.config.token_budget,
            tool_budget=self.config.tool_budget,
            timeout_seconds=self.config.timeout_seconds,
        )

        trajectory_id = uuid4()
        start_time = time.time()
        events: list[TrajectoryEvent] = []

        # Build initial messages
        messages: list[Message] = []
        if system:
            messages.append(Message(role="system", content=system))
        messages.append(Message(role="user", content=prompt))

        if self.verbose:
            logger.info(
                "Starting completion",
                trajectory_id=str(trajectory_id),
                prompt_length=len(prompt),
            )

        # Create sub-LLM tools if enabled
        extra_tools: list[Tool] | None = None
        if self.config.sub_calls_enabled:
            from rlm.tools.sub_llm import SubCallLimits, SubLLMContext, get_sub_llm_tools

            sub_context = SubLLMContext(
                limits=SubCallLimits(
                    enabled=self.config.sub_calls_enabled,
                    max_per_turn=self.config.sub_calls_max_per_turn,
                    budget_inheritance=self.config.sub_calls_budget_inheritance,
                    max_cost_per_session=self.config.sub_calls_max_cost_per_session,
                )
            )
            extra_tools = get_sub_llm_tools(
                rlm=self,
                context=sub_context,
                parent_options=options,
                parent_tokens_used=0,
            )

        # Execute recursive completion with timeout enforcement
        completion_error: str | None = None
        try:
            response, events = await asyncio.wait_for(
                self._recursive_complete(
                    messages=messages,
                    trajectory_id=trajectory_id,
                    parent_call_id=None,
                    depth=0,
                    options=options,
                    events=events,
                    extra_tools=extra_tools,
                ),
                timeout=float(options.timeout_seconds),
            )
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            logger.error(
                "Completion timed out",
                timeout=options.timeout_seconds,
                elapsed=elapsed,
                trajectory_id=str(trajectory_id),
            )
            raise TimeoutExceeded(
                elapsed_seconds=elapsed,
                timeout_seconds=options.timeout_seconds,
            ) from None
        except Exception as e:
            logger.error("Completion failed", error=str(e), trajectory_id=str(trajectory_id))
            completion_error = str(e)
            events.append(
                TrajectoryEvent(
                    trajectory_id=trajectory_id,
                    call_id=uuid4(),
                    parent_call_id=None,
                    depth=0,
                    prompt=prompt,
                    error=str(e),
                )
            )
            response = f"Error: {e}"

        # Calculate totals
        total_input_tokens = sum(e.input_tokens for e in events)
        total_output_tokens = sum(e.output_tokens for e in events)
        total_tokens = total_input_tokens + total_output_tokens
        total_tool_calls = sum(len(e.tool_calls) for e in events)
        duration_ms = int((time.time() - start_time) * 1000)

        # Calculate total cost (sum of event costs, or None if any are unknown)
        event_costs = [e.estimated_cost_usd for e in events]
        if all(c is not None for c in event_costs):
            total_cost_usd = sum(c for c in event_costs if c is not None)
        else:
            total_cost_usd = None

        # Log trajectory
        self.trajectory_logger.log_trajectory(trajectory_id, events)

        if self.verbose:
            from rlm.core.pricing import format_cost

            logger.info(
                "Completion finished",
                trajectory_id=str(trajectory_id),
                total_calls=len(events),
                total_tokens=total_tokens,
                total_cost=format_cost(total_cost_usd),
                duration_ms=duration_ms,
            )

        return RLMResult(
            response=response,
            trajectory_id=trajectory_id,
            total_calls=len(events),
            total_tokens=total_tokens,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            total_tool_calls=total_tool_calls,
            duration_ms=duration_ms,
            total_cost_usd=total_cost_usd,
            error=completion_error,
            events=events if options.include_trajectory else [],
        )

    async def _recursive_complete(
        self,
        messages: list[Message],
        trajectory_id: UUID,
        parent_call_id: UUID | None,
        depth: int,
        options: CompletionOptions,
        events: list[TrajectoryEvent],
        extra_tools: list[Tool] | None = None,
    ) -> tuple[str, list[TrajectoryEvent]]:
        """Internal recursive completion loop."""
        call_id = uuid4()

        # Check depth limit
        if depth >= options.max_depth:
            raise MaxDepthExceeded(depth=depth, max_depth=options.max_depth)

        # Check subcall limit
        if len(events) >= options.max_subcalls:
            raise MaxDepthExceeded(depth=len(events), max_depth=options.max_subcalls)

        # Check token budget
        current_tokens = sum(e.input_tokens + e.output_tokens for e in events)
        if current_tokens >= options.token_budget:
            raise TokenBudgetExhausted(tokens_used=current_tokens, budget=options.token_budget)

        # Check cost budget
        if options.cost_budget_usd is not None:
            current_cost = sum(e.estimated_cost_usd or 0 for e in events)
            if current_cost >= options.cost_budget_usd:
                raise CostBudgetExhausted(cost_used=current_cost, budget=options.cost_budget_usd)

        # Get available tools (registry + any extra tools for this completion)
        tools = self.tool_registry.get_all()
        if extra_tools:
            tools = tools + extra_tools

        # Call backend
        start_time = time.time()
        backend_kwargs: dict = {}
        if options.response_format is not None:
            backend_kwargs["response_format"] = options.response_format
        response = await self.backend.complete(messages, tools=tools, **backend_kwargs)
        duration_ms = int((time.time() - start_time) * 1000)

        # Calculate estimated cost for this call
        event_cost = estimate_cost(
            model=self.backend.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

        # Create event
        event = TrajectoryEvent(
            trajectory_id=trajectory_id,
            call_id=call_id,
            parent_call_id=parent_call_id,
            depth=depth,
            prompt=messages[-1].text_content,
            response=response.content,
            tool_calls=response.tool_calls,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            duration_ms=duration_ms,
            estimated_cost_usd=event_cost,
        )

        # Handle tool calls
        if response.tool_calls:
            tool_results: list[ToolResult] = []

            # Check how many tool calls we can still make
            current_tool_count = sum(len(e.tool_calls) for e in events)
            remaining_budget = options.tool_budget - current_tool_count

            # Filter tool calls to fit within budget
            allowed_calls = response.tool_calls[:remaining_budget]
            budget_exceeded_calls = response.tool_calls[remaining_budget:]

            if options.parallel_tools and len(allowed_calls) > 1:
                # Parallel execution with semaphore
                semaphore = asyncio.Semaphore(options.max_parallel)

                async def _execute_with_semaphore(tc: ToolCall) -> ToolResult:
                    async with semaphore:
                        return await self._execute_tool(tc, extra_tools=extra_tools)

                results = await asyncio.gather(
                    *[_execute_with_semaphore(tc) for tc in allowed_calls],
                    return_exceptions=True,
                )

                for tc, result in zip(allowed_calls, results, strict=True):
                    if isinstance(result, Exception):
                        tool_results.append(
                            ToolResult(
                                tool_call_id=tc.id,
                                content=f"Error: {result}",
                                is_error=True,
                            )
                        )
                    else:
                        tool_results.append(result)

                    if self.verbose:
                        is_error = isinstance(result, Exception) or (
                            isinstance(result, ToolResult) and result.is_error
                        )
                        logger.debug(
                            "Tool executed (parallel)",
                            tool=tc.name,
                            is_error=is_error,
                        )
            else:
                # Sequential execution (default)
                for tool_call in allowed_calls:
                    result = await self._execute_tool(tool_call, extra_tools=extra_tools)
                    tool_results.append(result)

                    if self.verbose:
                        logger.debug(
                            "Tool executed",
                            tool=tool_call.name,
                            is_error=result.is_error,
                        )

            # Add budget exceeded errors for remaining calls
            for tc in budget_exceeded_calls:
                tool_results.append(
                    ToolResult(
                        tool_call_id=tc.id,
                        content="Error: Tool budget exceeded. No more tool calls will be executed.",
                        is_error=True,
                    )
                )

            event.tool_results = tool_results
            events.append(event)

            # Add assistant message with tool calls
            messages.append(
                Message(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                )
            )

            # Add tool results
            for result in tool_results:
                messages.append(
                    Message(
                        role="tool",
                        content=result.content,
                        tool_call_id=result.tool_call_id,
                    )
                )

            # Recurse
            return await self._recursive_complete(
                messages=messages,
                trajectory_id=trajectory_id,
                parent_call_id=call_id,
                depth=depth + 1,
                options=options,
                events=events,
                extra_tools=extra_tools,
            )

        # No tool calls - we're done
        events.append(event)
        return response.content or "", events

    async def _execute_tool(
        self,
        tool_call: ToolCall,
        extra_tools: list[Tool] | None = None,
    ) -> ToolResult:
        """Execute a tool call."""
        try:
            tool = self.tool_registry.get(tool_call.name)
            # Check extra_tools if not found in registry
            if tool is None and extra_tools:
                for et in extra_tools:
                    if et.name == tool_call.name:
                        tool = et
                        break
            if tool is None:
                available = [t.name for t in self.tool_registry.get_all()]
                if extra_tools:
                    available.extend(t.name for t in extra_tools)
                error = ToolNotFoundError(tool_call.name, available)
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=str(error),
                    is_error=True,
                )

            # Execute tool handler
            result = await tool.execute(**tool_call.arguments)

            return ToolResult(
                tool_call_id=tool_call.id,
                content=str(result),
            )

        except ToolExecutionError as e:
            logger.error("Tool execution failed", tool=tool_call.name, error=str(e))
            return ToolResult(
                tool_call_id=tool_call.id,
                content=str(e),
                is_error=True,
            )
        except Exception as e:
            logger.error("Tool execution failed", tool=tool_call.name, error=str(e))
            exec_error = ToolExecutionError(tool_call.name, str(e), tool_call.arguments)
            return ToolResult(
                tool_call_id=tool_call.id,
                content=str(exec_error),
                is_error=True,
            )

    async def stream(
        self,
        prompt: str,
        system: str | None = None,
        options: StreamOptions | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a simple completion (no tool use).

        This method streams tokens as they arrive from the LLM.
        Note: Streaming does not support tool calls - use completion()
        for tasks that require tool use.

        Args:
            prompt: The user's prompt/question
            system: Optional system message for context
            options: Optional streaming options (cost budget, timeout)

        Yields:
            str: Content chunks as they arrive

        Raises:
            CostBudgetExhausted: If estimated cost exceeds budget before starting

        Example:
            ```python
            async for chunk in rlm.stream("Explain quantum computing"):
                print(chunk, end="", flush=True)
            ```
        """
        from rlm.core.pricing import estimate_cost

        options = options or StreamOptions()
        messages: list[Message] = []
        if system:
            messages.append(Message(role="system", content=system))
        messages.append(Message(role="user", content=prompt))

        # Estimate input tokens (rough: ~4 chars per token)
        total_chars = sum(len(m.content) for m in messages)
        estimated_input_tokens = total_chars // 4

        # Check cost budget before starting (input tokens only)
        if options.cost_budget_usd is not None:
            input_cost = estimate_cost(self.backend.model, estimated_input_tokens, 0)
            if input_cost is not None and input_cost >= options.cost_budget_usd:
                raise CostBudgetExhausted(
                    cost_used=input_cost,
                    budget=options.cost_budget_usd,
                )

        if self.verbose:
            logger.info(
                "Starting stream",
                prompt_length=len(prompt),
                estimated_input_tokens=estimated_input_tokens,
            )

        output_chars = 0
        async for chunk in self.backend.stream(messages):
            output_chars += len(chunk)
            yield chunk

        # Log final cost estimate
        estimated_output_tokens = output_chars // 4
        final_cost = estimate_cost(
            self.backend.model, estimated_input_tokens, estimated_output_tokens
        )
        if self.verbose:
            logger.info(
                "Stream completed",
                estimated_input_tokens=estimated_input_tokens,
                estimated_output_tokens=estimated_output_tokens,
                estimated_cost_usd=final_cost,
            )


class SniparaSandbox(RLM):
    """Public Snipara Sandbox class name.

    ``RLM`` remains available as a legacy class name for existing users.
    """
