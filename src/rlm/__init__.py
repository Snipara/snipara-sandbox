"""Legacy RLM import surface for Snipara Sandbox."""

from rlm.core.config import RLMConfig, load_config
from rlm.core.orchestrator import RLM, SniparaSandbox
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

__version__ = "2.2.2"

SniparaSandboxConfig = RLMConfig
Sandbox = SniparaSandbox

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
