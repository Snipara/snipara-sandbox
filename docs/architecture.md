# Snipara Sandbox Architecture

## Overview

Snipara Sandbox is a local-first execution environment for Recursive Language Models. It enables LLMs to:

1. **Decompose** complex tasks into sub-queries
2. **Execute** real code in sandboxed environments
3. **Retrieve** context on demand (via plugins like Snipara)
4. **Aggregate** results into coherent responses

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  User Application                                                           │
│  ├── CLI (snipara-sandbox run, snipara-sandbox logs, etc.)                                         │
│  ├── Python API (from snipara_sandbox import SniparaSandbox)                                      │
│  └── MCP Server (Claude Desktop/Code integration)                          │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Snipara Sandbox Orchestrator                                                           │
│  ├── Manages recursion depth and token budgets                              │
│  ├── Coordinates LLM calls and tool execution                               │
│  └── Logs trajectories for debugging                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│  LLM Backends                   │  REPL Environments                        │
│  ┌─────────────────────────┐    │  ┌─────────────────────────┐              │
│  │  LiteLLM (default)      │    │  │  Local REPL             │              │
│  │  - OpenAI               │    │  │  - RestrictedPython     │              │
│  │  - Anthropic            │    │  │  - Fast iteration       │              │
│  │  - 100+ providers       │    │  │  - Limited isolation    │              │
│  └─────────────────────────┘    │  └─────────────────────────┘              │
│                                 │  ┌─────────────────────────┐              │
│                                 │  │  Docker REPL            │              │
│                                 │  │  - Full isolation       │              │
│                                 │  │  - Resource limits      │              │
│                                 │  │  - Network disabled     │              │
│                                 │  └─────────────────────────┘              │
│                                 │  ┌─────────────────────────┐              │
│                                 │  │  WebAssembly REPL       │              │
│                                 │  │  - Pyodide sandbox      │              │
│                                 │  │  - No Docker needed     │              │
│                                 │  │  - Portable             │              │
│                                 │  └─────────────────────────┘              │
├─────────────────────────────────────────────────────────────────────────────┤
│  Tool Registry                                                              │
│  ├── Builtin: execute_code, file_read, list_files                          │
│  ├── Snipara: snipara_context_query, snipara_search, snipara_sections, snipara_read,     │
│  │   snipara_shared_context + memory tools (native HTTP or snipara-mcp)       │
│  └── Custom: user-defined tools                                            │
├─────────────────────────────────────────────────────────────────────────────┤
│  MCP Server (zero API keys, for Claude Code)                               │
│  ├── execute_python: Sandboxed code execution                              │
│  ├── get_repl_context / set_repl_context: Persistent state                 │
│  └── clear_repl_context: Reset execution context                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Core Components

### Orchestrator

The `Snipara Sandbox` class is the main entry point. It coordinates:

- **Message management** - Builds and maintains conversation history
- **Tool dispatch** - Routes tool calls to appropriate handlers
- **Recursion control** - Enforces depth and budget limits
- **Logging** - Records trajectories for debugging

```python
from snipara_sandbox import SniparaSandbox

sandbox = SniparaSandbox(
    backend="litellm",
    model="gpt-4o-mini",
    environment="docker",
)

result = await sandbox.completion("Analyze the logs")
```

### Backends

Backends abstract LLM providers. The default `LiteLLMBackend` supports 100+ providers.

```python
from snipara_sandbox.backends.litellm import LiteLLMBackend

# Use any LiteLLM-supported model
backend = LiteLLMBackend(model="claude-3-sonnet-20240229")
```

### REPL Environments

Three execution environments are provided:

| Environment | Isolation | Speed | Use Case |
|-------------|-----------|-------|----------|
| **Local** | Limited (RestrictedPython) | Fast | Development, trusted code |
| **Docker** | Full (container) | Slower | Production, untrusted code |
| **WebAssembly** | Full (Pyodide sandbox) | Medium | Browser/serverless, portable |

#### WebAssembly REPL

The WebAssembly environment uses Pyodide to run Python in a sandboxed WebAssembly runtime:

```python
sandbox = SniparaSandbox(environment="wasm")
```

Features:
- **Full isolation** - Runs in WebAssembly sandbox
- **No Docker required** - Works anywhere WebAssembly runs
- **Package installation** - Supports micropip for pure-Python packages
- **Portable** - Same execution across platforms

### Tool System

Tools are functions the LLM can call. They follow the OpenAI function calling format.

```python
from snipara_sandbox.backends.base import Tool

async def my_tool(param: str) -> dict:
    return {"result": param.upper()}

tool = Tool(
    name="my_tool",
    description="Convert to uppercase",
    parameters={
        "type": "object",
        "properties": {
            "param": {"type": "string"}
        },
        "required": ["param"]
    },
    handler=my_tool,
)

sandbox = SniparaSandbox(tools=[tool])
```

## Execution Flow

```
1. User sends prompt
   │
2. Orchestrator builds messages
   │
3. Backend generates completion
   │
   ├── No tool calls → Return response
   │
   └── Has tool calls
       │
       4. Execute each tool
       │
       5. Add results to messages
       │
       6. Recurse (go to step 3)
       │
       └── Until: no tools OR max_depth OR max_subcalls
```

## Safety Model

### Local REPL (RestrictedPython)

- **Import whitelist** - Only safe modules (json, re, math, etc.)
- **Attribute guards** - Controlled attribute access
- **Output limits** - Truncation at 100KB
- **No network** - Network modules blocked

### Docker REPL

- **Process isolation** - Separate container
- **Network disabled** - `--network none` by default
- **Resource limits** - CPU and memory caps
- **Read-only mounts** - Workspace mounted as read-only
- **Prepared workspace images** - Optional cached image build installs repo deps before isolated execution
- **Non-root user** - Runs as unprivileged user

## Trajectory Logging

Every completion generates a trajectory log (JSONL):

```json
{"_type": "trajectory_metadata", "trajectory_id": "abc-123", "event_count": 3}
{"trajectory_id": "abc-123", "call_id": "...", "depth": 0, "prompt": "...", "response": "..."}
{"trajectory_id": "abc-123", "call_id": "...", "depth": 1, "prompt": "...", "tool_calls": [...]}
```

View with CLI:
```bash
snipara-sandbox logs              # Recent trajectories
snipara-sandbox logs abc-123      # Specific trajectory
```

## Streaming

Snipara Sandbox supports streaming completions for real-time output:

```python
async for chunk in sandbox.stream("Write a story about a robot"):
    print(chunk, end="", flush=True)
```

Note: Streaming is for simple completions without tool use. For tool-using completions, use the standard `completion()` method.

## Trajectory Visualizer

The trajectory visualizer provides a web-based dashboard for debugging:

```bash
# Launch visualizer
snipara-sandbox visualize

# Custom port and log directory
snipara-sandbox visualize --dir ./my-logs --port 8080
```

Features:
- **Execution tree** - Visual representation of recursive calls
- **Token usage** - Charts showing token consumption
- **Tool analysis** - Breakdown of tool calls and timing
- **Event inspector** - Detailed view of each execution step

## Plugin System

### Snipara Integration

Snipara tools are auto-registered via the native HTTP client (preferred)
or the `snipara-mcp` package (fallback). Auth resolves from OAuth tokens,
`SNIPARA_API_KEY` env var, or `snipara_api_key` in config:

```python
sandbox = SniparaSandbox(
    snipara_api_key="snp-...",
    snipara_project_slug="my-project",
)
# Tools available: snipara_context_query, snipara_search, snipara_sections,
# snipara_read, snipara_shared_context (+ snipara_remember/recall/memories/forget
# when memory_enabled=True)
```

See [Snipara Integration Guide](snipara.md) for full details.

### Custom Tools

Register custom tools via the constructor or registry:

```python
# Via constructor
sandbox = SniparaSandbox(tools=[my_tool])

# Via registry
sandbox.tool_registry.register(my_tool)
```

## MCP Server

The MCP (Model Context Protocol) server enables Claude Desktop and Claude Code to use Snipara Sandbox capabilities.

### Components

```
┌─────────────────────────────────────────────────────────────┐
│  Claude Desktop / Claude Code                               │
└─────────────────────────────────┬───────────────────────────┘
                                  │ stdio (JSON-RPC)
                                  ▼
┌─────────────────────────────────────────────────────────────┐
│  MCP Server (snipara-sandbox mcp-serve) - Zero API keys                 │
│  ├── LocalREPL: Persistent sandboxed execution             │
│  └── Tools: execute_python, get/set/clear_repl_context     │
└─────────────────────────────────────────────────────────────┘
```

## Error Handling

Snipara Sandbox uses a comprehensive exception hierarchy for clear error handling:

```
RLMError (base)
├── Budget Errors
│   ├── MaxDepthExceeded
│   ├── TokenBudgetExhausted
│   ├── ToolBudgetExhausted
│   └── TimeoutExceeded
├── REPL Errors
│   ├── REPLExecutionError
│   ├── REPLTimeoutError
│   ├── REPLImportError
│   └── REPLSecurityError
├── Tool Errors
│   ├── ToolNotFoundError
│   ├── ToolExecutionError
│   ├── ToolValidationError
│   └── SniparaAPIError
├── Backend Errors
│   ├── BackendConnectionError
│   ├── BackendRateLimitError
│   └── BackendAuthError
└── Config Errors
    ├── ConfigNotFoundError
    └── ConfigValidationError
```

All exceptions include context for debugging:

```python
from snipara_sandbox.core.exceptions import MaxDepthExceeded

try:
    result = await sandbox.completion(prompt)
except MaxDepthExceeded as e:
    print(f"Recursion limit: depth={e.depth}, max={e.max_depth}")
```

### Available MCP Tools

The MCP server provides sandboxed code execution for Claude Code (zero API keys required):

| Tool | Description |
|------|-------------|
| `execute_python` | Sandboxed Python execution (RestrictedPython) |
| `get_repl_context` | Get persistent REPL variables |
| `set_repl_context` | Set REPL variables |
| `clear_repl_context` | Reset REPL state |

For context retrieval, use **snipara-mcp** separately (with OAuth authentication).

### Security Model

The MCP server uses the same RestrictedPython sandbox as the Local REPL:

- Import whitelist (json, re, math, datetime, etc.)
- No file I/O, network, or system access
- Output truncation at 100KB
- Execution timeout (default: 30s, max: 60s)

See [MCP Integration Guide](mcp-integration.md) for full documentation.

## Configuration

### Environment Variables

```bash
# Backend
SNIPARA_SANDBOX_BACKEND=litellm
SNIPARA_SANDBOX_MODEL=gpt-4o-mini
SNIPARA_SANDBOX_TEMPERATURE=0.0

# Environment
SNIPARA_SANDBOX_ENVIRONMENT=docker
SNIPARA_SANDBOX_DOCKER_IMAGE=python:3.11-slim

# Limits
SNIPARA_SANDBOX_MAX_DEPTH=4
SNIPARA_SANDBOX_TOKEN_BUDGET=8000

# Snipara (optional)
SNIPARA_API_KEY=snp-...
SNIPARA_PROJECT_SLUG=my-project
```

### Config File (snipara-sandbox.toml)

```toml
[snipara_sandbox]
backend = "litellm"
model = "gpt-4o-mini"
environment = "docker"
max_depth = 4
token_budget = 8000
```

## Performance Considerations

1. **Use Docker for production** - Better isolation, predictable resource usage
2. **Set appropriate budgets** - Prevent runaway recursion
3. **Cache when possible** - Snipara caches context queries
4. **Log trajectories** - Debug performance issues with trajectory analysis
