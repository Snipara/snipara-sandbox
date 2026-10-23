"""Core RLM components."""

from rlm.core.config import RLMConfig, load_config
from rlm.core.exceptions import (
    BackendAuthError,
    BackendConnectionError,
    BackendError,
    BackendRateLimitError,
    ConfigError,
    ConfigNotFoundError,
    ConfigValidationError,
    CostBudgetExhausted,
    MaxDepthExceeded,
    REPLError,
    REPLExecutionError,
    REPLImportError,
    REPLSecurityError,
    REPLTimeoutError,
    RLMError,
    TimeoutExceeded,
    TokenBudgetExhausted,
    ToolBudgetExhausted,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolValidationError,
)
from rlm.core.types import (
    CompletionOptions,
    Message,
    REPLResult,
    RLMResult,
    StreamOptions,
    ToolCall,
    ToolResult,
    TrajectoryEvent,
)

__all__ = [
    # Core classes
    "RLM",
    "RLMConfig",
    "load_config",
    # Types
    "CompletionOptions",
    "Message",
    "REPLResult",
    "RLMResult",
    "StreamOptions",
    "ToolCall",
    "ToolResult",
    "TrajectoryEvent",
    # Exceptions
    "RLMError",
    "MaxDepthExceeded",
    "TokenBudgetExhausted",
    "ToolBudgetExhausted",
    "CostBudgetExhausted",
    "TimeoutExceeded",
    "REPLError",
    "REPLExecutionError",
    "REPLTimeoutError",
    "REPLImportError",
    "REPLSecurityError",
    "ToolError",
    "ToolNotFoundError",
    "ToolExecutionError",
    "ToolValidationError",
    "BackendError",
    "BackendConnectionError",
    "BackendRateLimitError",
    "BackendAuthError",
    "ConfigError",
    "ConfigNotFoundError",
    "ConfigValidationError",
]


def __getattr__(name: str):
    if name == "RLM":
        from rlm.core.orchestrator import RLM

        globals()[name] = RLM
        return RLM
    raise AttributeError(name)
