"""Snipara Sandbox public Python API.

The legacy ``rlm`` package remains available for existing users. New code can
import from ``snipara_sandbox`` while the underlying implementation migrates.
"""

import importlib
import sys

import rlm as _rlm
from rlm import *  # noqa: F403
from rlm import RLMConfig, SniparaSandbox, __version__

Sandbox = SniparaSandbox
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
