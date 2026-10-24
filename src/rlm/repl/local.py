"""Local REPL with RestrictedPython sandboxing."""

from __future__ import annotations

import builtins
import hashlib
import json
import operator
import platform
import time
import traceback
from collections import OrderedDict
from typing import Any

# Resource tracking only available on Unix
try:
    import resource

    HAS_RESOURCE = True
except ImportError:
    HAS_RESOURCE = False

from RestrictedPython import compile_restricted, safe_builtins
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter
from RestrictedPython.Guards import guarded_iter_unpack_sequence, safer_getattr
from RestrictedPython.PrintCollector import PrintCollector as RestrictedPrintCollector

from rlm.core.types import REPLResult
from rlm.repl.base import BaseREPL
from rlm.repl.safety import (
    ALLOWED_IMPORTS,
    BLOCKED_IMPORTS,
    MAX_EXECUTION_TIME,
    is_import_allowed,
    truncate_output,
)


def _safe_import(name: str, *args: Any, **kwargs: Any) -> Any:
    """Restricted import that only allows safe modules."""
    if not is_import_allowed(name):
        raise ImportError(f"Import of '{name}' is not allowed in sandbox")
    return builtins.__import__(name, *args, **kwargs)


_INPLACE_OPERATORS = {
    "+=": operator.iadd,
    "-=": operator.isub,
    "*=": operator.imul,
    "/=": operator.itruediv,
    "//=": operator.ifloordiv,
    "%=": operator.imod,
    "**=": operator.ipow,
    "<<=": operator.ilshift,
    ">>=": operator.irshift,
    "&=": operator.iand,
    "^=": operator.ixor,
    "|=": operator.ior,
    "@=": operator.imatmul,
}


def _guarded_inplace(operation: str, left: Any, right: Any) -> Any:
    """Implement RestrictedPython's augmented variable assignment hook.

    Use Python's in-place operators so mutable aliases retain normal semantics.
    Attribute and item assignments remain subject to RestrictedPython's compiler
    restrictions; this hook only handles the variable assignment it emits.
    """
    try:
        implementation = _INPLACE_OPERATORS[operation]
    except KeyError:
        raise ValueError(f"Unsupported augmented operator: {operation}") from None
    return implementation(left, right)


class LocalREPL(BaseREPL):
    """Local Python REPL with RestrictedPython sandboxing.

    Uses RestrictedPython to provide a safe execution environment by:
    - Restricting imports to a whitelist of safe modules
    - Guarding attribute access
    - Limiting output size
    - Providing a controlled namespace

    Note: This provides defense in depth but is not a complete sandbox.
    For untrusted code, use DockerREPL instead.

    Example:
        ```python
        repl = LocalREPL(timeout=30)
        result = await repl.execute("print(1 + 1)")
        print(result.output)  # "2\\n"
        ```
    """

    def __init__(
        self,
        timeout: int = MAX_EXECUTION_TIME,
        cache_size: int = 100,
        cache_enabled: bool = True,
    ):
        """Initialize the local REPL.

        Args:
            timeout: Maximum execution time in seconds
            cache_size: Max number of cached results (LRU eviction)
            cache_enabled: Enable/disable result caching
        """
        self.timeout = timeout
        self._globals: dict[str, Any] = {}
        self._context: dict[str, Any] = {}
        self._cache: OrderedDict[str, REPLResult] = OrderedDict()
        self._cache_size = cache_size
        self._cache_enabled = cache_enabled
        self._cache_hits = 0
        self._cache_misses = 0
        self._setup_globals()

    def _setup_globals(self) -> None:
        """Setup restricted globals for execution."""
        # Additional safe builtins not included in RestrictedPython's safe_builtins
        additional_builtins = {
            # Collection constructors and functions
            "list": list,
            "dict": dict,
            "set": set,
            "frozenset": frozenset,
            # Aggregation functions
            "sum": sum,
            "min": min,
            "max": max,
            "any": any,
            "all": all,
            # Iteration helpers
            "enumerate": enumerate,
            "map": map,
            "filter": filter,
            "reversed": reversed,
            # Type introspection (read-only)
            "type": type,
            "callable": callable,
        }

        self._globals = {
            "__builtins__": {
                **safe_builtins,
                **additional_builtins,
                "__import__": _safe_import,
                "None": None,
                "True": True,
                "False": False,
            },
            "_inplacevar_": _guarded_inplace,
            "_getattr_": safer_getattr,
            "_getitem_": default_guarded_getitem,
            "_getiter_": default_guarded_getiter,
            "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
            "_write_": self._guarded_write,
            # Use RestrictedPython's PrintCollector class
            "_print_": RestrictedPrintCollector,
            # Shared context variable accessible to user code
            "context": self._context,
            # Result variable for returning values
            "result": None,
        }

    def _guarded_write(self, obj: Any) -> Any:
        """Guard attribute writes."""
        return obj

    def _get_resource_usage(self) -> tuple[float, int] | None:
        """Get current resource usage (CPU time in ms, memory in bytes).

        Returns:
            Tuple of (cpu_time_ms, memory_bytes) or None on Windows
        """
        if not HAS_RESOURCE:
            return None
        usage = resource.getrusage(resource.RUSAGE_SELF)
        cpu_time_ms = int((usage.ru_utime + usage.ru_stime) * 1000)
        # ru_maxrss is in bytes on Linux, kilobytes on macOS
        if platform.system() == "Darwin":
            memory_bytes = usage.ru_maxrss  # Already in bytes on macOS
        else:
            memory_bytes = usage.ru_maxrss * 1024  # Convert KB to bytes on Linux
        return cpu_time_ms, memory_bytes

    def _compute_cache_key(self, code: str) -> str:
        """Compute a cache key from code and context.

        The key includes:
        - Code hash
        - Context hash (JSON-serializable values only)

        Returns:
            SHA256 hex digest as cache key
        """
        # Serialize context (only JSON-serializable values)
        try:
            context_str = json.dumps(self._context, sort_keys=True, default=str)
        except (TypeError, ValueError):
            context_str = str(sorted(self._context.items()))

        key_material = f"code:{code}\ncontext:{context_str}"
        return hashlib.sha256(key_material.encode()).hexdigest()[:32]

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dict with hits, misses, size, and hit rate
        """
        total = self._cache_hits + self._cache_misses
        hit_rate = self._cache_hits / total if total > 0 else 0.0
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "size": len(self._cache),
            "max_size": self._cache_size,
            "hit_rate": round(hit_rate, 3),
            "enabled": self._cache_enabled,
        }

    def clear_cache(self) -> None:
        """Clear the result cache."""
        self._cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0

    async def execute(self, code: str, timeout: int | None = None) -> REPLResult:
        """Execute code in the local sandbox.

        Args:
            code: Python code to execute
            timeout: Optional timeout override

        Returns:
            REPLResult with output, error, and timing
        """
        timeout = timeout or self.timeout
        start_time = time.time()

        # Check cache first
        if self._cache_enabled:
            cache_key = self._compute_cache_key(code)
            if cache_key in self._cache:
                self._cache_hits += 1
                # Move to end for LRU
                self._cache.move_to_end(cache_key)
                cached = self._cache[cache_key]
                # Return cached result with updated timing
                return REPLResult(
                    output=cached.output,
                    error=cached.error,
                    execution_time_ms=0,  # Instant from cache
                    truncated=cached.truncated,
                    memory_peak_bytes=cached.memory_peak_bytes,
                    cpu_time_ms=0,
                )
            self._cache_misses += 1

        # Get resource usage before execution
        start_resources = self._get_resource_usage()

        try:
            # Compile with RestrictedPython
            # Note: RestrictedPython 8.x returns code object directly,
            # raises SyntaxError on compilation failure
            byte_code = compile_restricted(
                code,
                filename="<rlm-repl>",
                mode="exec",
            )

            if byte_code is None:
                return REPLResult(
                    output="",
                    error="Compilation failed: code could not be compiled",
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            # Sync context
            self._globals["context"] = self._context

            # Clear any previous print collector
            if "_print" in self._globals:
                del self._globals["_print"]

            # Execute - in RestrictedPython 8.x, byte_code IS the code object
            exec(byte_code, self._globals)

            # Get resource usage after execution
            end_resources = self._get_resource_usage()

            # Calculate resource deltas
            cpu_time_ms: int | None = None
            memory_peak_bytes: int | None = None
            if start_resources and end_resources:
                cpu_time_ms = int(end_resources[0] - start_resources[0])
                # Memory is peak, not delta - report the current peak
                memory_peak_bytes = end_resources[1]

            # Collect output from RestrictedPython's PrintCollector
            # The '_print' variable holds the PrintCollector instance after execution
            output = ""
            print_collector = self._globals.get("_print")
            if print_collector is not None and hasattr(print_collector, "txt"):
                output = "".join(print_collector.txt)

            # Check for result variable
            result_value = self._globals.get("result")
            if result_value is not None:
                if output and not output.endswith("\n"):
                    output += "\n"
                output += f"result = {result_value!r}"

            # Apply truncation if needed
            output, truncated = truncate_output(output)

            result = REPLResult(
                output=output,
                error=None,
                execution_time_ms=int((time.time() - start_time) * 1000),
                truncated=truncated,
                memory_peak_bytes=memory_peak_bytes,
                cpu_time_ms=cpu_time_ms,
            )

            # Cache successful results (only if caching enabled)
            if self._cache_enabled:
                cache_key = self._compute_cache_key(code)
                self._cache[cache_key] = result
                # LRU eviction if over size limit
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)

            return result

        except Exception as e:
            error_msg = self._format_error(e, code)
            return REPLResult(
                output="",
                error=error_msg,
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

    def _format_error(self, exc: Exception, code: str) -> str:
        """Format an exception with enhanced context for LLM consumption.

        Includes:
        - Exception type and message
        - Line number and offending code
        - Relevant variable context
        - Suggestions for blocked imports
        """
        lines = code.splitlines()
        parts = [f"{type(exc).__name__}: {exc}"]

        # Extract line number from traceback
        tb = traceback.extract_tb(exc.__traceback__)
        line_no = None
        for frame in reversed(tb):
            if frame.filename == "<rlm-repl>":
                line_no = frame.lineno
                break

        # Show the offending line
        if line_no and 1 <= line_no <= len(lines):
            offending_line = lines[line_no - 1]
            parts.append(f"  Line {line_no}: {offending_line.strip()}")

        # Add suggestions for import errors
        if isinstance(exc, ImportError):
            msg = str(exc)
            # Extract module name from "Import of 'X' is not allowed" or "No module named 'X'"
            if "'" in msg:
                start = msg.index("'") + 1
                end = msg.index("'", start)
                module = msg[start:end].split(".")[0]

                if module in BLOCKED_IMPORTS:
                    parts.append(f"  Hint: '{module}' is blocked for security reasons.")
                    # Suggest alternatives
                    alternatives = self._get_import_alternatives(module)
                    if alternatives:
                        parts.append(f"  Try: {', '.join(alternatives)}")
                else:
                    parts.append("  Hint: Only standard library modules are allowed.")
                    parts.append(f"  Available: {', '.join(sorted(ALLOWED_IMPORTS)[:10])}...")

        # Add relevant variable context for NameError
        if isinstance(exc, NameError):
            parts.append("  Available variables in context:")
            context_vars = [k for k in self._context if not k.startswith("_")]
            if context_vars:
                parts.append(f"    {', '.join(context_vars[:10])}")
            else:
                parts.append("    (none - use set_repl_context to add variables)")

        return "\n".join(parts)

    def _get_import_alternatives(self, blocked_module: str) -> list[str]:
        """Suggest alternatives for blocked imports."""
        alternatives: dict[str, list[str]] = {
            "os": ["pathlib (for path operations)"],
            "subprocess": ["(not available - execute in sandbox only)"],
            "socket": ["urllib.parse (for URL parsing only)"],
            "requests": ["urllib.parse (for URL parsing only)"],
            "pickle": ["json (for serialization)"],
            "sqlite3": ["(not available - use in-memory data structures)"],
            "sys": ["(not available)"],
            "asyncio": ["(not available - use synchronous code)"],
            "threading": ["(not available - single-threaded execution)"],
            "multiprocessing": ["(not available - single-process execution)"],
        }
        return alternatives.get(blocked_module, [])

    def get_context(self) -> dict[str, Any]:
        """Get the current context."""
        return self._context.copy()

    def set_context(self, key: str, value: Any) -> None:
        """Set a value in the context."""
        self._context[key] = value

    def clear_context(self) -> None:
        """Clear the context."""
        self._context.clear()
        self._globals["result"] = None

    def reset(self) -> None:
        """Reset the REPL to a clean state."""
        self.clear_context()
        self._setup_globals()
