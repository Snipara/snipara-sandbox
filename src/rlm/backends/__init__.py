"""LLM backend adapters."""

from rlm.backends.base import BackendResponse, BaseBackend, Tool

__all__ = [
    "BackendResponse",
    "BaseBackend",
    "Tool",
    "LiteLLMBackend",
]


def __getattr__(name: str):
    if name == "LiteLLMBackend":
        from rlm.backends.litellm import LiteLLMBackend

        globals()[name] = LiteLLMBackend
        return LiteLLMBackend
    raise AttributeError(name)
