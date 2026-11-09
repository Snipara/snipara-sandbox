"""Snipara Sandbox public Python API.

The legacy ``rlm`` package remains available for existing users. New code can
import from ``snipara_sandbox`` while the underlying implementation migrates.
"""

import importlib
import sys

import rlm as _rlm
from rlm import RLMConfig, __version__

SniparaSandboxConfig = RLMConfig
Config = RLMConfig

for _submodule in (
    "agent",
    "backends",
    "core",
    "logging",
    "repl",
    "tools",
    "validation",
):
    sys.modules[f"{__name__}.{_submodule}"] = importlib.import_module(f"rlm.{_submodule}")

__all__ = [
    *_rlm.__all__,
    "__version__",
    "SniparaSandbox",
    "Sandbox",
    "SniparaSandboxConfig",
    "Config",
]


def __getattr__(name: str):
    """Forward public names without eagerly loading the inference runtime."""
    if name in _rlm.__all__:
        value = getattr(_rlm, name)
        globals()[name] = value
        return value
    raise AttributeError(name)
