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
import sys
import time
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    TextContent,
    Tool,
)

from rlm.core.config import EXECUTION_PROFILES, load_config
from rlm.repl.local import LocalREPL
from rlm.repl.localdev import LocalDevREPL

# Default session TTL: 30 minutes
DEFAULT_SESSION_TTL = 30 * 60


@dataclass
class Session:
    """A REPL session with metadata."""

    id: str
    repl: LocalREPL | LocalDevREPL
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

    def __init__(self, ttl: float = DEFAULT_SESSION_TTL, trust_level: str = "sandboxed"):
        self._sessions: dict[str, Session] = {}
        self._ttl = ttl
        self._trust_level = trust_level
        self._default_session_id = "default"
        # Create default session
        self._sessions[self._default_session_id] = Session(
            id=self._default_session_id,
            repl=self._create_repl(),
            trust_level=self._trust_level,
        )

    def _create_repl(self) -> LocalREPL | LocalDevREPL:
        """Create a REPL based on trust level."""
        if self._trust_level == "local":
            return LocalDevREPL(timeout=300, audit_log=True)
        else:
            # Default to sandboxed (RestrictedPython)
            return LocalREPL(timeout=30)

    def get_or_create(self, session_id: str | None = None) -> Session:
        """Get existing session or create new one."""
        session_id = session_id or self._default_session_id

        # Cleanup expired sessions periodically
        self._cleanup_expired()

        if session_id not in self._sessions:
            self._sessions[session_id] = Session(
                id=session_id,
                repl=self._create_repl(),
                trust_level=self._trust_level,
            )

        session = self._sessions[session_id]
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
            self._sessions[self._default_session_id] = Session(
                id=self._default_session_id,
                repl=self._create_repl(),
                trust_level=self._trust_level,
            )
            return True
        return self._sessions.pop(session_id, None) is not None

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
                "trust_level": s.trust_level,
                "created_at": s.created_at,
                "last_access": s.last_access,
                "age_seconds": int(now - s.created_at),
                "idle_seconds": int(now - s.last_access),
                "context_keys": list(s.repl.get_context().keys()),
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

    # Load config to get trust_level
    try:
        config = load_config()
        trust_level = config.trust_level
    except Exception:
        trust_level = "sandboxed"

    # Session manager for multi-session support
    sessions = SessionManager(trust_level=trust_level)
    agents = AgentManager()

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def list_tools() -> list[Tool]:
        """List available Snipara Sandbox tools."""
        return [
            Tool(
                name="execute_python",
                description=(
                    "Execute Python code in a sandboxed environment. "
                    "Trust level determines capabilities:\n"
                    "- sandboxed (default): RestrictedPython, safe stdlib only\n"
                    "- local: Full access to files, subprocess, network, any library\n\n"
                    "In sandboxed mode: json, re, math, datetime, collections, itertools allowed. "
                    "In local mode: Full Python environment with project libraries.\n\n"
                    "Use 'result = <value>' to return a value. Use print() for output. "
                    "Context persists across calls. "
                    "Use session_id for separate contexts. "
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

        if name == "execute_python":
            session = sessions.get_or_create(session_id)
            return await _execute_python(session.repl, arguments, session.trust_level)

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


async def _execute_python(
    repl: LocalREPL | LocalDevREPL, arguments: dict[str, Any], trust_level: str = "sandboxed"
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
            isError=True,
        )

    result = await repl.execute(code, timeout=timeout)

    if result.success:
        output = result.output or "(no output)"
        if result.truncated:
            output += "\n... (output truncated)"
        # Add trust level indicator for local mode
        if trust_level == "local":
            output = f"[local mode] {output}"
        return CallToolResult(
            content=[TextContent(type="text", text=output)],
        )
    else:
        error_prefix = "[local mode] " if trust_level == "local" else ""
        return CallToolResult(
            content=[TextContent(type="text", text=f"{error_prefix}Error: {result.error}")],
            isError=True,
        )


async def _get_repl_context(repl: LocalREPL) -> CallToolResult:
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


async def _set_repl_context(repl: LocalREPL, arguments: dict[str, Any]) -> CallToolResult:
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

    repl.set_context(key, value)

    return CallToolResult(
        content=[TextContent(type="text", text=f"Set context['{key}'] = {repr(value)}")],
    )


async def _clear_repl_context(repl: LocalREPL) -> CallToolResult:
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
        trust_indicator = " [LOCAL]" if s["trust_level"] == "local" else ""
        lines.append(
            f"  - {s['id']}{trust_indicator}: idle {s['idle_seconds']}s, age {s['age_seconds']}s{context_info}"
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
            config = load_config()
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
