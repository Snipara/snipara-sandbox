"""Core types for RLM runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

UTC = timezone.utc


class Environment(str, Enum):
    """REPL execution environment."""

    LOCAL = "local"
    DOCKER = "docker"


class Backend(str, Enum):
    """LLM backend provider."""

    LITELLM = "litellm"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class SearchMode(str, Enum):
    """Search mode for context queries."""

    KEYWORD = "keyword"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


@dataclass
class Message:
    """A message in the conversation.

    Content can be a plain string or a list of content blocks for multi-modal
    input (images, audio). List format follows the OpenAI content block spec:

        [
            {"type": "text", "text": "What's in this image?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
        ]
    """

    role: str  # "user", "assistant", "system", "tool"
    content: str | list[dict[str, Any]]
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None  # For tool messages

    @property
    def text_content(self) -> str:
        """Extract text content regardless of format.

        Returns the content as a string. If content is a list of blocks,
        concatenates all text blocks.
        """
        if isinstance(self.content, str):
            return self.content
        return " ".join(
            block.get("text", "")
            for block in self.content
            if isinstance(block, dict) and block.get("type") == "text"
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            result["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id
        if self.name:
            result["name"] = self.name
        return result


@dataclass
class ToolCall:
    """A tool call from the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
        }


@dataclass
class ToolResult:
    """Result from a tool execution."""

    tool_call_id: str
    content: str
    is_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "tool_call_id": self.tool_call_id,
            "content": self.content,
            "is_error": self.is_error,
        }


@dataclass
class REPLResult:
    """Result from REPL code execution."""

    output: str
    error: str | None = None
    execution_time_ms: int = 0
    truncated: bool = False
    memory_peak_bytes: int | None = None  # Peak memory usage during execution
    cpu_time_ms: int | None = None  # CPU time consumed

    @property
    def success(self) -> bool:
        """Whether execution succeeded."""
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "output": self.output,
            "error": self.error,
            "execution_time_ms": self.execution_time_ms,
            "truncated": self.truncated,
            "memory_peak_bytes": self.memory_peak_bytes,
            "cpu_time_ms": self.cpu_time_ms,
        }


@dataclass
class TrajectoryEvent:
    """A single event in the execution trajectory."""

    trajectory_id: UUID
    call_id: UUID
    parent_call_id: UUID | None
    depth: int
    prompt: str
    response: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    repl_results: list[REPLResult] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    error: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    estimated_cost_usd: float | None = None  # Estimated API cost for this event
    sub_call_type: str | None = None  # "sub_complete" or "batch_complete" for sub-LLM calls

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "trajectory_id": str(self.trajectory_id),
            "call_id": str(self.call_id),
            "parent_call_id": str(self.parent_call_id) if self.parent_call_id else None,
            "depth": self.depth,
            "prompt": self.prompt,
            "response": self.response,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "tool_results": [tr.to_dict() for tr in self.tool_results],
            "repl_results": [rr.to_dict() for rr in self.repl_results],
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "timestamp": self.timestamp.isoformat(),
            "estimated_cost_usd": self.estimated_cost_usd,
        }
        if self.sub_call_type:
            result["sub_call_type"] = self.sub_call_type
        return result


@dataclass
class RLMResult:
    """Result from a recursive completion."""

    response: str
    trajectory_id: UUID
    total_calls: int
    total_tokens: int
    total_tool_calls: int
    duration_ms: int
    events: list[TrajectoryEvent] = field(default_factory=list)
    total_input_tokens: int = 0  # Total input/prompt tokens across all calls
    total_output_tokens: int = 0  # Total output/completion tokens across all calls
    total_cost_usd: float | None = None  # Total estimated API cost
    error: str | None = None

    @property
    def success(self) -> bool:
        """Whether the completion finished without any recorded errors."""
        return self.error is None and all(e.error is None for e in self.events)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "response": self.response,
            "trajectory_id": str(self.trajectory_id),
            "total_calls": self.total_calls,
            "total_tokens": self.total_tokens,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tool_calls": self.total_tool_calls,
            "duration_ms": self.duration_ms,
            "total_cost_usd": self.total_cost_usd,
            "error": self.error,
            "success": self.success,
            "events": [e.to_dict() for e in self.events],
        }


@dataclass
class StreamOptions:
    """Options for streaming completion requests."""

    cost_budget_usd: float | None = None  # Maximum API cost in USD
    timeout_seconds: int = 120

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "cost_budget_usd": self.cost_budget_usd,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class CompletionOptions:
    """Options for a completion request."""

    max_depth: int = 4
    max_subcalls: int = 12
    token_budget: int = 8000
    tool_budget: int = 20
    timeout_seconds: int = 120
    include_trajectory: bool = False
    temperature: float | None = None
    stop_sequences: list[str] | None = None
    cost_budget_usd: float | None = None  # Maximum API cost in USD
    response_format: dict[str, Any] | None = None  # JSON schema for structured outputs
    parallel_tools: bool = False  # Execute tool calls concurrently
    max_parallel: int = 5  # Maximum concurrent tool executions

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "max_depth": self.max_depth,
            "max_subcalls": self.max_subcalls,
            "token_budget": self.token_budget,
            "tool_budget": self.tool_budget,
            "timeout_seconds": self.timeout_seconds,
            "include_trajectory": self.include_trajectory,
            "temperature": self.temperature,
            "stop_sequences": self.stop_sequences,
            "cost_budget_usd": self.cost_budget_usd,
            "response_format": self.response_format,
            "parallel_tools": self.parallel_tools,
            "max_parallel": self.max_parallel,
        }
