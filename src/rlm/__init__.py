"""Legacy RLM import surface for Snipara Sandbox."""

from rlm.core.config import RLMConfig, load_config
from rlm.core.types import (
    CompletionOptions,
    Message,
    REPLResult,
    RLMResult,
    ToolCall,
    ToolResult,
    TrajectoryEvent,
)
from rlm.tools.base import Tool
from rlm.tools.registry import ToolRegistry

__version__ = "2.4.1"

SniparaSandboxConfig = RLMConfig

__all__ = [
    # Main class
    "RLM",
    "SniparaSandbox",
    "Sandbox",
    # Types
    "CompletionOptions",
    "Message",
    "REPLResult",
    "RLMResult",
    "ToolCall",
    "ToolResult",
    "TrajectoryEvent",
    # Config
    "RLMConfig",
    "SniparaSandboxConfig",
    "load_config",
    # Tools
    "Tool",
    "ToolRegistry",
]


def __getattr__(name: str):
    # Deterministic validation must not import provider integrations or fetch
    # pricing metadata merely to start a CLI or inspect a request.
    if name in {"RLM", "SniparaSandbox", "Sandbox"}:
        from rlm.core.orchestrator import RLM, SniparaSandbox

        values = {"RLM": RLM, "SniparaSandbox": SniparaSandbox, "Sandbox": SniparaSandbox}
        globals().update(values)
        return values[name]
    raise AttributeError(name)
