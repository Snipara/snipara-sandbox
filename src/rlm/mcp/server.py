"""MCP Server implementation for Snipara Sandbox.

This module provides an MCP (Model Context Protocol) server that exposes
a sandboxed Python execution environment to Claude Desktop, Claude Code,
and other MCP clients.

Zero API keys required - designed to work within Claude Code's billing.
For Snipara context retrieval, use snipara-mcp separately (with OAuth).

Tools provided:
- execute_python: Run Python code in a sandboxed environment
- get_repl_context: Get the current REPL context
- set_repl_context: Set variables in the REPL context
- clear_repl_context: Clear the REPL context
- list_sessions: List active REPL sessions
- destroy_session: Destroy a REPL session
- snipara_agent_run: Start an autonomous agent task
- snipara_agent_status: Check agent run status
- snipara_agent_cancel: Cancel a running agent
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    TextContent,
    Tool,
)

from rlm.core.config import EXECUTION_PROFILES, RLMConfig, load_config
from rlm.repl.base import BaseREPL
from rlm.repl.docker import DockerREPL, build_workspace_image
from rlm.repl.local import LocalREPL
from rlm.repl.localdev import LocalDevREPL
from rlm.validation.mcp import NAMES as VALIDATION_NAMES
from rlm.validation.mcp import handle as handle_validation
from rlm.validation.mcp import validation_tools

# Default session TTL: 30 minutes
DEFAULT_SESSION_TTL = 30 * 60
VALIDATION_ROOT = Path(
    os.environ.get("SNIPARA_SANDBOX_VALIDATION_WORKSPACE") or Path.cwd()
).resolve()
VALIDATION_ENABLED = os.environ.get("SNIPARA_SANDBOX_VALIDATION_ENABLED", "1") == "1"
SUPPORTED_EXECUTION_ENVIRONMENTS = frozenset({"sandbox", "docker", "local"})
ENVIRONMENT_ALIASES = {
    "sandboxed": "sandbox",
}


@dataclass
class Session:
    """A REPL session with metadata."""

    id: str
    repl: BaseREPL | None = None
    environment: str | None = None
    trust_level: str = "sandboxed"
    created_at: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)

    def touch(self) -> None:
        """Update last access time."""
        self.last_access = time.time()

    def is_expired(self, ttl: float) -> bool:
        """Check if session has expired."""
        return time.time() - self.last_access > ttl


class SessionManager:
    """Manages multiple REPL sessions with TTL-based cleanup."""

    def __init__(
        self,
        ttl: float = DEFAULT_SESSION_TTL,
        trust_level: str = "sandboxed",
        environment: str = "local",
        config: RLMConfig | None = None,
    ):
        self._sessions: dict[str, Session] = {}
        self._ttl = ttl
        self._config = config
        self._trust_level = config.trust_level if config is not None else trust_level
        self._configured_environment = config.environment if config is not None else environment
        self._default_session_id = "default"
        # Create an unbound default session so the first execute_python call can
        # choose an explicit environment without silently discarding context.
        self._sessions[self._default_session_id] = Session(
            id=self._default_session_id,
            trust_level=self._trust_level,
        )

    def _normalize_environment(self, environment: str) -> str:
        """Normalize aliases and validate the requested execution environment."""
        normalized = ENVIRONMENT_ALIASES.get(environment, environment)
        if normalized not in SUPPORTED_EXECUTION_ENVIRONMENTS:
            supported = ", ".join(sorted(SUPPORTED_EXECUTION_ENVIRONMENTS))
            raise ValueError(
                f"Unknown environment '{environment}'. Supported environments: {supported}"
            )
        return normalized

    def _resolve_default_environment(self) -> str:
        """Resolve the default environment when execute_python omits it."""
        configured = ENVIRONMENT_ALIASES.get(
            self._configured_environment, self._configured_environment
        )
        if configured == "docker":
            return "docker"
        if configured == "local":
            return "local" if self._trust_level == "local" else "sandbox"
        if configured == "sandbox":
            return "sandbox"
        if configured == "wasm":
            raise ValueError(
                "MCP execute_python does not support the 'wasm' environment. "
                "Use environment='sandbox', 'docker', or 'local'."
            )
        if self._trust_level == "docker":
            return "docker"
        if self._trust_level == "local":
            return "local"
        return "sandbox"

    def _resolve_docker_workdir_mount(self) -> Path | None:
        """Resolve the host workspace mounted read-only into Docker executions."""
        if self._config is None:
            return Path.cwd()
        if not self._config.docker_mount_workspace:
            return None
        mount_path = self._config.docker_workspace_path or Path.cwd()
        return mount_path.resolve()

    def _resolve_docker_image(self) -> str:
        """Resolve the Docker image, optionally preparing a workspace image."""
        if self._config is None:
            return "python:3.11-slim"
        setup_mode = self._config.docker_workspace_setup
        if setup_mode == "none":
            return self._config.docker_image
        workspace_path = self._config.docker_workspace_path or Path.cwd()
        return build_workspace_image(
            base_image=self._config.docker_image,
            workspace_path=workspace_path,
            setup_mode=setup_mode,
            install_command=self._config.docker_workspace_install_command,
            install_extras=self._config.docker_workspace_install_extras,
        )

    def _create_repl(self, environment: str) -> BaseREPL:
        """Create a REPL for the requested environment."""
        if environment == "sandbox":
            return LocalREPL(timeout=30)
        if environment == "local":
            if self._trust_level != "local":
                raise ValueError(
                    'Local execution requires trust_level = "local" in the Snipara Sandbox config.'
                )
            return LocalDevREPL(timeout=300, audit_log=True)
        if environment == "docker":
            if self._config is None:
                return DockerREPL()
            return DockerREPL(
                image=self._resolve_docker_image(),
                cpus=self._config.docker_cpus,
                memory=self._config.docker_memory,
                timeout=self._config.docker_timeout,
                network_disabled=self._config.docker_network_disabled,
                workdir_mount=self._resolve_docker_workdir_mount(),
            )
        supported = ", ".join(sorted(SUPPORTED_EXECUTION_ENVIRONMENTS))
        raise ValueError(
            f"Unknown environment '{environment}'. Supported environments: {supported}"
        )

    def _bind_session(self, session: Session, environment: str) -> None:
        """Bind a session to an execution environment on first use."""
        session.environment = environment
        session.repl = self._create_repl(environment)

    def _release_session(self, session: Session) -> None:
        """Release any resources held by a session REPL."""
        if session.repl is None:
            return
        cleanup = getattr(session.repl, "cleanup", None)
        if callable(cleanup):
            cleanup()

    def get_or_create(
        self, session_id: str | None = None, requested_environment: str | None = None
    ) -> Session:
        """Get existing session or create new one."""
        session_id = session_id or self._default_session_id

        # Cleanup expired sessions periodically
        self._cleanup_expired()

        session = self._sessions.get(session_id)
        if session is None:
            session = Session(id=session_id, trust_level=self._trust_level)
            self._sessions[session_id] = session

        if session.environment is None:
            environment = self._normalize_environment(
                requested_environment or self._resolve_default_environment()
            )
            self._bind_session(session, environment)
        elif requested_environment is not None:
            environment = self._normalize_environment(requested_environment)
            if session.environment != environment:
                raise ValueError(
                    f"Session '{session_id}' is bound to environment '{session.environment}'. "
                    f"Requested '{environment}'. Use a different session_id or destroy the "
                    "existing session first."
                )

        session.touch()
        return session

    def get(self, session_id: str) -> Session | None:
        """Get session by ID without creating."""
        session = self._sessions.get(session_id)
        if session:
            session.touch()
        return session

    def destroy(self, session_id: str) -> bool:
        """Destroy a session. Returns True if session existed."""
        if session_id == self._default_session_id:
            # Reset default session instead of destroying
            self._release_session(self._sessions[self._default_session_id])
            self._sessions[self._default_session_id] = Session(
                id=self._default_session_id,
                trust_level=self._trust_level,
            )
            return True
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        self._release_session(session)
        return True

    @property
    def trust_level(self) -> str:
        """Get current trust level."""
        return self._trust_level

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all active sessions with metadata."""
        self._cleanup_expired()
        now = time.time()
        return [
            {
                "id": s.id,
                "environment": s.environment
                or ENVIRONMENT_ALIASES.get(
                    self._configured_environment, self._configured_environment
                ),
                "trust_level": s.trust_level,
                "created_at": s.created_at,
                "last_access": s.last_access,
                "age_seconds": int(now - s.created_at),
                "idle_seconds": int(now - s.last_access),
                "context_keys": list(s.repl.get_context().keys()) if s.repl else [],
            }
            for s in self._sessions.values()
        ]

    def _cleanup_expired(self) -> None:
        """Remove expired sessions (except default)."""
        expired = [
            sid
            for sid, s in self._sessions.items()
            if sid != self._default_session_id and s.is_expired(self._ttl)
        ]
        for sid in expired:
            self._release_session(self._sessions[sid])
            del self._sessions[sid]


@dataclass
class AgentRun:
    """A running or completed agent task."""

    run_id: str
    task: str
    future: asyncio.Task[Any]
    started_at: float = field(default_factory=time.time)
    result: Any = None  # AgentResult when complete
    error: str | None = None


class AgentManager:
    """Manages autonomous agent runs."""

    def __init__(self) -> None:
        self._runs: dict[str, AgentRun] = {}

    def start(self, run_id: str, task: str, coro: Any) -> AgentRun:
        """Start an agent run as an async task."""
        loop = asyncio.get_event_loop()
        future = loop.create_task(coro)
        run = AgentRun(run_id=run_id, task=task, future=future)

        # Add callback to capture result/error
        def _on_done(fut: asyncio.Task[Any]) -> None:
            try:
                run.result = fut.result()
            except Exception as e:
                run.error = str(e)

        future.add_done_callback(_on_done)
        self._runs[run_id] = run
        return run

    def get(self, run_id: str) -> AgentRun | None:
        """Get an agent run by ID."""
        return self._runs.get(run_id)

    def cancel(self, run_id: str) -> bool:
        """Cancel a running agent. Returns True if found."""
        run = self._runs.get(run_id)
        if run is None:
            return False
        if not run.future.done():
            run.future.cancel()
        return True

    def list_runs(self) -> list[dict[str, Any]]:
        """List all agent runs with status."""
        results = []
        for run in self._runs.values():
            status = "running"
            if run.future.done():
                status = "completed" if run.error is None else "error"
            elif run.future.cancelled():
                status = "cancelled"
            results.append(
                {
                    "run_id": run.run_id,
                    "task": run.task[:100],
                    "status": status,
                    "started_at": run.started_at,
                    "elapsed_seconds": int(time.time() - run.started_at),
                }
            )
        return results


async def _run_agent_stdio_safe(coro: Any) -> Any:
    """Run an agent task without allowing prints/logs to corrupt MCP stdout."""
    with redirect_stdout(sys.stderr):
        return await coro


def create_server() -> Server:
    """Create and configure the MCP server with code sandbox tools."""
    server = Server("SniparaSandbox")

    # Load config to get trust level and execution environment defaults.
    try:
        config = load_config(project_env=False)
    except Exception:
        config = RLMConfig(_env_file=None)

    # Session manager for multi-session support
    sessions = SessionManager(config=config)
    agents = AgentManager()

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def list_tools() -> list[Tool]:
        """List available Snipara Sandbox tools."""
        return [
            *validation_tools(),
            Tool(
                name="execute_python",
                description=(
                    "Execute Python code in an isolated session. "
                    "Choose the environment per call or omit it to use the config default.\n"
                    "- sandbox: RestrictedPython, safe stdlib only\n"
                    "- docker: Container isolation with DockerREPL\n"
                    "- local: Full access to files, subprocess, network, any library "
                    "(requires trust_level='local')\n\n"
                    "In sandbox mode: json, re, math, datetime, collections, itertools allowed. "
                    "In local mode: Full Python environment with project libraries.\n\n"
                    "Use 'result = <value>' to return a value. Use print() for output. "
                    "Context persists across calls. A session_id stays bound to the first "
                    "environment it uses. "
                    "Profiles: quick (5s), default (30s), analysis (120s), extended (300s)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "Python code to execute",
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Timeout in seconds (overrides profile). Max: 300",
                        },
                        "profile": {
                            "type": "string",
                            "enum": ["quick", "default", "analysis", "extended"],
                            "description": "Execution profile: quick (5s), default (30s), analysis (120s), extended (300s)",
                            "default": "default",
                        },
                        "environment": {
                            "type": "string",
                            "enum": ["sandbox", "docker", "local"],
                            "description": (
                                "Execution environment. Omit to use the server config default. "
                                "A session_id stays bound to the first environment it uses."
                            ),
                        },
                        "session_id": {
                            "type": "string",
                            "description": "Session ID for isolated context (default: 'default')",
                        },
                    },
                    "required": ["code"],
                },
            ),
            Tool(
                name="get_repl_context",
                description=(
                    "Get the current REPL context. Returns all variables stored "
                    "in the persistent execution context from previous execute_python calls."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Session ID (default: 'default')",
                        },
                    },
                },
            ),
            Tool(
                name="set_repl_context",
                description=(
                    "Set a variable in the REPL context. The variable will persist "
                    "across multiple execute_python calls. Value should be JSON-encoded."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Variable name",
                        },
                        "value": {
                            "type": "string",
                            "description": "JSON-encoded value to store",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "Session ID (default: 'default')",
                        },
                    },
                    "required": ["key", "value"],
                },
            ),
            Tool(
                name="clear_repl_context",
                description="Clear all variables from the REPL context and reset to clean state.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Session ID (default: 'default')",
                        },
                    },
                },
            ),
            Tool(
                name="list_sessions",
                description=(
                    "List all active REPL sessions with metadata including "
                    "creation time, last access, and stored variable names."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="destroy_session",
                description=(
                    "Destroy a REPL session and free its resources. "
                    "The 'default' session is reset instead of destroyed."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Session ID to destroy",
                        },
                    },
                    "required": ["session_id"],
                },
            ),
            Tool(
                name="snipara_agent_run",
                description=(
                    "Start an autonomous agent that iteratively solves a task. "
                    "The agent loops: observe -> think -> act -> terminate. "
                    "Uses REPL for code execution, Snipara for context, and "
                    "sub-LLM calls for delegation. Returns a run_id to check status."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The task for the agent to solve",
                        },
                        "max_iterations": {
                            "type": "integer",
                            "description": "Maximum iterations (default: 10, max: 50)",
                            "default": 10,
                        },
                        "token_budget": {
                            "type": "integer",
                            "description": "Token budget (default: 50000)",
                            "default": 50000,
                        },
                        "cost_limit": {
                            "type": "number",
                            "description": "Cost limit in USD (default: 2.0, max: 10.0)",
                            "default": 2.0,
                        },
                    },
                    "required": ["task"],
                },
            ),
            Tool(
                name="snipara_agent_status",
                description=(
                    "Check the status of an autonomous agent run. "
                    "Returns running/completed/error status and the result if done."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "The agent run ID from snipara_agent_run",
                        },
                    },
                    "required": ["run_id"],
                },
            ),
            Tool(
                name="snipara_agent_cancel",
                description="Cancel a running autonomous agent.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "The agent run ID to cancel",
                        },
                    },
                    "required": ["run_id"],
                },
            ),
            Tool(
                name="rlm_agent_run",
                description="Legacy alias for snipara_agent_run.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The task for the agent to solve",
                        },
                        "max_iterations": {
                            "type": "integer",
                            "description": "Maximum iterations (default: 10, max: 50)",
                            "default": 10,
                        },
                        "token_budget": {
                            "type": "integer",
                            "description": "Token budget (default: 50000)",
                            "default": 50000,
                        },
                        "cost_limit": {
                            "type": "number",
                            "description": "Cost limit in USD (default: 2.0, max: 10.0)",
                            "default": 2.0,
                        },
                    },
                    "required": ["task"],
                },
            ),
            Tool(
                name="rlm_agent_status",
                description="Legacy alias for snipara_agent_status.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "The agent run ID from snipara_agent_run",
                        },
                    },
                    "required": ["run_id"],
                },
            ),
            Tool(
                name="rlm_agent_cancel",
                description="Legacy alias for snipara_agent_cancel.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "The agent run ID to cancel",
                        },
                    },
                    "required": ["run_id"],
                },
            ),
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        """Handle tool calls."""
        session_id = arguments.get("session_id")

        if name in VALIDATION_NAMES:
            if not VALIDATION_ENABLED:
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text="Repository validation requires a valid operator configuration with workspace access enabled.",
                        )
                    ],
                    isError=True,
                )
            return await handle_validation(name, arguments, VALIDATION_ROOT)

        if name == "execute_python":
            try:
                session = sessions.get_or_create(
                    session_id, requested_environment=arguments.get("environment")
                )
            except (ImportError, ValueError) as e:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"Error: {e}")],
                    isError=True,
                )
            if session.repl is None or session.environment is None:
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text="Error: Session failed to initialize execution environment",
                        )
                    ],
                    isError=True,
                )
            return await _execute_python(
                session.repl,
                arguments,
                session.trust_level,
                session.environment,
                session.id,
            )

        elif name == "get_repl_context":
            session = sessions.get_or_create(session_id)
            return await _get_repl_context(session.repl)

        elif name == "set_repl_context":
            session = sessions.get_or_create(session_id)
            return await _set_repl_context(session.repl, arguments)

        elif name == "clear_repl_context":
            session = sessions.get_or_create(session_id)
            return await _clear_repl_context(session.repl)

        elif name == "list_sessions":
            return await _list_sessions(sessions)

        elif name == "destroy_session":
            return await _destroy_session(sessions, arguments)

        elif name in {"snipara_agent_run", "rlm_agent_run"}:
            return await _agent_run(agents, sessions, arguments)

        elif name in {"snipara_agent_status", "rlm_agent_status"}:
            return await _agent_status(agents, arguments)

        elif name in {"snipara_agent_cancel", "rlm_agent_cancel"}:
            return await _agent_cancel(agents, arguments)

        else:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True,
            )

    return server


def _build_execution_receipt(
    *,
    environment: str,
    repl: BaseREPL,
    trust_level: str,
    profile: str,
    timeout: int,
    session_id: str,
    success: bool,
    execution_time_ms: int = 0,
    cpu_time_ms: int | None = None,
    memory_peak_bytes: int | None = None,
    truncated: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    """Build structured receipt metadata for execute_python responses."""
    return {
        "environment": environment,
        "repl_backend": type(repl).__name__,
        "trust_level": trust_level,
        "profile": profile,
        "timeout_seconds": timeout,
        "session_id": session_id,
        "success": success,
        "execution_time_ms": execution_time_ms,
        "cpu_time_ms": cpu_time_ms,
        "memory_peak_bytes": memory_peak_bytes,
        "truncated": truncated,
        "error": error,
    }


async def _execute_python(
    repl: BaseREPL,
    arguments: dict[str, Any],
    trust_level: str = "sandboxed",
    environment: str = "sandbox",
    session_id: str = "default",
) -> CallToolResult:
    """Execute Python code in the sandbox or local dev environment."""
    code = arguments.get("code", "")

    # Determine timeout from profile or explicit value
    profile_name = arguments.get("profile", "default")
    profile = EXECUTION_PROFILES.get(profile_name, EXECUTION_PROFILES["default"])
    timeout = arguments.get("timeout") or profile.timeout
    timeout = min(timeout, 300)  # Cap at 300 seconds (extended profile max)

    if not code.strip():
        return CallToolResult(
            content=[TextContent(type="text", text="Error: No code provided")],
            structuredContent=_build_execution_receipt(
                environment=environment,
                repl=repl,
                trust_level=trust_level,
                profile=profile_name,
                timeout=timeout,
                session_id=session_id,
                success=False,
                error="No code provided",
            ),
            isError=True,
        )

    result = await repl.execute(code, timeout=timeout)

    if result.success:
        output = result.output or "(no output)"
        if result.truncated:
            output += "\n... (output truncated)"
        # Add trust level indicator for local mode
        if environment == "local":
            output = f"[local mode] {output}"
        return CallToolResult(
            content=[TextContent(type="text", text=output)],
            structuredContent=_build_execution_receipt(
                environment=environment,
                repl=repl,
                trust_level=trust_level,
                profile=profile_name,
                timeout=timeout,
                session_id=session_id,
                success=True,
                execution_time_ms=result.execution_time_ms,
                cpu_time_ms=result.cpu_time_ms,
                memory_peak_bytes=result.memory_peak_bytes,
                truncated=result.truncated,
            ),
        )
    else:
        error_prefix = "[local mode] " if environment == "local" else ""
        return CallToolResult(
            content=[TextContent(type="text", text=f"{error_prefix}Error: {result.error}")],
            structuredContent=_build_execution_receipt(
                environment=environment,
                repl=repl,
                trust_level=trust_level,
                profile=profile_name,
                timeout=timeout,
                session_id=session_id,
                success=False,
                execution_time_ms=result.execution_time_ms,
                cpu_time_ms=result.cpu_time_ms,
                memory_peak_bytes=result.memory_peak_bytes,
                truncated=result.truncated,
                error=result.error,
            ),
            isError=True,
        )


async def _get_repl_context(repl: BaseREPL) -> CallToolResult:
    """Get the current REPL context."""
    context = repl.get_context()

    if not context:
        return CallToolResult(
            content=[TextContent(type="text", text="Context is empty")],
        )

    # Format context as readable output
    lines = ["Current REPL context:"]
    for key, value in context.items():
        try:
            value_str = json.dumps(value, indent=2, default=str)
        except (TypeError, ValueError):
            value_str = repr(value)
        lines.append(f"  {key} = {value_str}")

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
    )


async def _set_repl_context(repl: BaseREPL, arguments: dict[str, Any]) -> CallToolResult:
    """Set a variable in the REPL context."""
    key = arguments.get("key", "")
    value_str = arguments.get("value", "")

    if not key:
        return CallToolResult(
            content=[TextContent(type="text", text="Error: No key provided")],
            isError=True,
        )

    try:
        value = json.loads(value_str)
    except json.JSONDecodeError:
        # If not valid JSON, store as string
        value = value_str

    try:
        repl.set_context(key, value)
    except ValueError as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Error: {e}")],
            isError=True,
        )

    return CallToolResult(
        content=[TextContent(type="text", text=f"Set context['{key}'] = {repr(value)}")],
    )


async def _clear_repl_context(repl: BaseREPL) -> CallToolResult:
    """Clear the REPL context."""
    repl.clear_context()
    return CallToolResult(
        content=[TextContent(type="text", text="REPL context cleared")],
    )


async def _list_sessions(sessions: SessionManager) -> CallToolResult:
    """List all active sessions."""
    session_list = sessions.list_sessions()

    if not session_list:
        return CallToolResult(
            content=[TextContent(type="text", text="No active sessions")],
        )

    lines = [f"Active sessions ({len(session_list)}):"]
    lines.append(f"Trust level: {sessions.trust_level}")
    lines.append("")
    for s in session_list:
        context_info = f", context: {s['context_keys']}" if s["context_keys"] else ""
        trust_indicator = " [LOCAL]" if s["environment"] == "local" else ""
        lines.append(
            f"  - {s['id']} ({s['environment']}){trust_indicator}: "
            f"idle {s['idle_seconds']}s, age {s['age_seconds']}s{context_info}"
        )

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
    )


async def _destroy_session(sessions: SessionManager, arguments: dict[str, Any]) -> CallToolResult:
    """Destroy a session."""
    session_id = arguments.get("session_id", "")

    if not session_id:
        return CallToolResult(
            content=[TextContent(type="text", text="Error: No session_id provided")],
            isError=True,
        )

    if sessions.destroy(session_id):
        if session_id == "default":
            return CallToolResult(
                content=[TextContent(type="text", text="Default session reset")],
            )
        return CallToolResult(
            content=[TextContent(type="text", text=f"Session '{session_id}' destroyed")],
        )
    else:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Session '{session_id}' not found")],
            isError=True,
        )


async def _agent_run(
    agents: AgentManager, sessions: SessionManager, arguments: dict[str, Any]
) -> CallToolResult:
    """Start an autonomous agent run."""
    task = arguments.get("task", "")
    if not task.strip():
        return CallToolResult(
            content=[TextContent(type="text", text="Error: No task provided")],
            isError=True,
        )

    try:
        from uuid import uuid4

        from rlm.agent.config import AgentConfig
        from rlm.agent.runner import AgentRunner
        from rlm.core.config import load_config
        from rlm.core.orchestrator import RLM

        with redirect_stdout(sys.stderr):
            config = load_config(project_env=False)
            rlm = RLM(config=config)

            agent_config = AgentConfig(
                max_iterations=arguments.get("max_iterations", 10),
                token_budget=arguments.get("token_budget", 50000),
                cost_limit=arguments.get("cost_limit", 2.0),
            )

            runner = AgentRunner(rlm, agent_config)
            run_id = str(uuid4())[:8]
            run_coro = _run_agent_stdio_safe(runner.run(task))

            agents.start(run_id, task, run_coro)

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "run_id": run_id,
                            "status": "running",
                            "task": task[:200],
                            "config": {
                                "max_iterations": agent_config.max_iterations,
                                "token_budget": agent_config.token_budget,
                                "cost_limit": agent_config.cost_limit,
                            },
                        }
                    ),
                )
            ],
        )

    except ImportError as e:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"Error: Agent dependencies not available: {e}",
                )
            ],
            isError=True,
        )
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Error starting agent: {e}")],
            isError=True,
        )


async def _agent_status(agents: AgentManager, arguments: dict[str, Any]) -> CallToolResult:
    """Check agent run status."""
    run_id = arguments.get("run_id", "")
    if not run_id:
        return CallToolResult(
            content=[TextContent(type="text", text="Error: No run_id provided")],
            isError=True,
        )

    run = agents.get(run_id)
    if run is None:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Error: Run '{run_id}' not found")],
            isError=True,
        )

    if run.future.done():
        if run.error:
            result_data = {
                "run_id": run_id,
                "status": "error",
                "error": run.error,
                "elapsed_seconds": int(time.time() - run.started_at),
            }
        else:
            result_obj = run.result
            result_data = {
                "run_id": run_id,
                "status": "completed",
                "result": result_obj.to_dict()
                if hasattr(result_obj, "to_dict")
                else str(result_obj),
                "elapsed_seconds": int(time.time() - run.started_at),
            }
    else:
        result_data = {
            "run_id": run_id,
            "status": "running",
            "task": run.task[:200],
            "elapsed_seconds": int(time.time() - run.started_at),
        }

    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(result_data, indent=2))],
    )


async def _agent_cancel(agents: AgentManager, arguments: dict[str, Any]) -> CallToolResult:
    """Cancel a running agent."""
    run_id = arguments.get("run_id", "")
    if not run_id:
        return CallToolResult(
            content=[TextContent(type="text", text="Error: No run_id provided")],
            isError=True,
        )

    if agents.cancel(run_id):
        return CallToolResult(
            content=[TextContent(type="text", text=f"Agent run '{run_id}' cancelled")],
        )
    else:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Error: Run '{run_id}' not found")],
            isError=True,
        )


def run_server() -> None:
    """Run the MCP server using stdio transport."""
    server = create_server()

    async def main() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(main())


if __name__ == "__main__":
    run_server()
