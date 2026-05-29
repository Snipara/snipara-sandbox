# API Reference

This document provides a comprehensive API reference for Snipara Sandbox.

## Table of Contents

- [Main Classes](#main-classes)
- [Types](#types)
- [Configuration](#configuration)
- [Exceptions](#exceptions)
- [Tool System](#tool-system)
- [CLI Commands](#cli-commands)

---

## Main Classes

### Snipara Sandbox

The main entry point for Snipara Sandbox.

```python
from snipara_sandbox import SniparaSandbox

sandbox = SniparaSandbox(
    # Required
    model: str = "gpt-4o-mini",          # LLM model identifier

    # Backend
    backend: str = "litellm",            # "litellm", "openai", or "anthropic"
    api_key: str | None = None,          # Provider API key

    # Execution Environment
    environment: str = "local",          # "local", "docker", or "wasm"

    # Recursion Limits
    max_depth: int = 4,                  # Max recursive depth
    max_subcalls: int = 12,              # Max total tool calls
    token_budget: int = 8000,            # Token limit per completion

    # Docker Settings (when environment="docker")
    docker_image: str = "python:3.11-slim",
    docker_cpus: float = 1.0,
    docker_memory: str = "512m",
    docker_network_disabled: bool = True,
    docker_timeout: int = 30,

    # Tools
    tools: list[Tool] | None = None,     # Custom tools

    # Snipara Integration
    snipara_api_key: str | None = None,
    snipara_project_slug: str | None = None,
    snipara_base_url: str = "https://api.snipara.com/mcp",
    memory_enabled: bool = False,

    # Logging
    verbose: bool = False,
    log_dir: str = "./logs",
)
```

#### Methods

##### completion

```python
async def completion(
    self,
    prompt: str,
    system: str | None = None,
    options: CompletionOptions | None = None,
) -> RLMResult:
```

Execute a recursive completion.

**Parameters:**
- `prompt` - The user prompt
- `system` - Optional system prompt
- `options` - Optional completion options

**Returns:** `RLMResult` with response, trajectory, and metrics

**Example:**
```python
result = await sandbox.completion("Analyze the CSV file")
print(result.response)
```

##### stream

```python
async def stream(
    self,
    prompt: str,
    system: str | None = None,
) -> AsyncIterator[str]:
```

Stream completion tokens.

**Note:** Streaming only works for simple completions without tool calls.

**Example:**
```python
async for chunk in sandbox.stream("Write a story"):
    print(chunk, end="", flush=True)
```

##### agent_run

```python
async def agent_run(
    self,
    goal: str,
    max_iterations: int = 10,
    cost_limit: float | None = None,
    system: str | None = None,
) -> AgentResult:
```

Run an autonomous agent.

**Parameters:**
- `goal` - The agent's goal
- `max_iterations` - Maximum iterations
- `cost_limit` - Maximum cost in USD
- `system` - Optional system prompt

**Returns:** `AgentResult` with final state and trajectory

---

## Types

### CompletionOptions

```python
@dataclass
class CompletionOptions:
    max_depth: int = 4
    max_subcalls: int = 12
    token_budget: int = 8000
    tool_budget: int = 20
    timeout_seconds: int = 120
    include_trajectory: bool = False
    temperature: float | None = None
    stop_sequences: list[str] | None = None
    cost_budget_usd: float | None = None
    response_format: dict[str, Any] | None = None
    parallel_tools: bool = False
    max_parallel: int = 5
```

### RLMResult

```python
@dataclass
class RLMResult:
    response: str                      # Final response
    trajectory_id: UUID                # Unique execution ID
    total_calls: int                   # Number of LLM calls
    total_tokens: int                  # Total tokens used
    total_tool_calls: int              # Tool calls made
    duration_ms: int                   # Execution time
    events: list[TrajectoryEvent]      # Execution trace
    total_input_tokens: int = 0        # Input tokens total
    total_output_tokens: int = 0       # Output tokens total
    total_cost_usd: float | None = None  # Estimated cost
```

### Message

```python
@dataclass
class Message:
    role: str                          # "user", "assistant", "system", "tool"
    content: str | list[dict[str, Any]]  # Message content
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
```

### ToolCall

```python
@dataclass
class ToolCall:
    id: str                            # Unique call ID
    name: str                          # Tool name
    arguments: dict[str, Any]          # Tool arguments
```

### ToolResult

```python
@dataclass
class ToolResult:
    tool_call_id: str                  # Associated call ID
    content: str                       # Result content
    is_error: bool = False             # Whether this is an error
```

### REPLResult

```python
@dataclass
class REPLResult:
    output: str                        # Execution output
    error: str | None = None           # Error message
    execution_time_ms: int = 0         # Execution time
    truncated: bool = False            # Output truncated
    memory_peak_bytes: int | None = None  # Peak memory
    cpu_time_ms: int | None = None     # CPU time
```

### TrajectoryEvent

```python
@dataclass
class TrajectoryEvent:
    trajectory_id: UUID
    call_id: UUID
    parent_call_id: UUID | None
    depth: int
    prompt: str
    response: str | None = None
    tool_calls: list[ToolCall]
    tool_results: list[ToolResult]
    repl_results: list[REPLResult]
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    error: str | None = None
    timestamp: datetime
    estimated_cost_usd: float | None = None
    sub_call_type: str | None = None
```

---

## Configuration

### SniparaSandboxConfig

```python
class SniparaSandboxConfig(BaseSettings):
    # Backend settings
    backend: str = "litellm"
    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    api_key: str | None = None

    # Environment settings
    environment: str = "local"
    docker_image: str = "python:3.11-slim"
    docker_cpus: float = 1.0
    docker_memory: str = "512m"
    docker_network_disabled: bool = True
    docker_mount_workspace: bool = True
    docker_workspace_path: Path | None = None
    docker_timeout: int = 30

    # Limits
    max_depth: int = 4
    max_subcalls: int = 12
    token_budget: int = 8000
    tool_budget: int = 20
    timeout_seconds: int = 120
    parallel_tools: bool = False
    max_parallel: int = 5

    # Security
    allowed_paths: list[Path] = Field(default_factory=list)

    # Logging
    log_dir: Path = Field(default_factory=lambda: Path("./logs"))
    verbose: bool = False
    log_level: str = "INFO"

    # Snipara
    snipara_api_key: str | None = None
    snipara_project_slug: str | None = None
    snipara_base_url: str = "https://api.snipara.com/mcp"
    memory_enabled: bool = False

    @property
    def snipara_enabled(self) -> bool:
        """Check if Snipara is configured."""
        return bool(self.snipara_api_key and self.snipara_project_slug)
```

### load_config

```python
def load_config(config_path: Path | None = None) -> SniparaSandboxConfig:
```

Load configuration from file and environment.

**Parameters:**
- `config_path` - Optional path to snipara-sandbox.toml

**Priority:**
1. Environment variables
2. Config file
3. Default values

---

## Exceptions

### Base Exception

```python
class RLMError(Exception):
    message: str
    context: dict[str, Any]
```

### Budget Errors

```python
class MaxDepthExceeded(RLMError):
    depth: int
    max_depth: int

class TokenBudgetExhausted(RLMError):
    tokens_used: int
    budget: int

class CostBudgetExhausted(RLMError):
    cost_used: float
    budget: float

class ToolBudgetExhausted(RLMError):
    calls_made: int
    budget: int

class TimeoutExceeded(RLMError):
    elapsed_seconds: float
    timeout_seconds: int
```

### REPL Errors

```python
class REPLError(RLMError): pass

class REPLExecutionError(REPLError):
    code: str
    error: str
    output: str

class REPLTimeoutError(REPLError):
    code: str
    timeout: int

class REPLImportError(REPLError):
    module: str
    allowed: list[str]

class REPLSecurityError(REPLError):
    violation: str
```

### Tool Errors

```python
class ToolError(RLMError): pass

class ToolNotFoundError(ToolError):
    tool_name: str
    available_tools: list[str]

class ToolExecutionError(ToolError):
    tool_name: str
    error: str
    arguments: dict[str, Any]

class ToolValidationError(ToolError):
    tool_name: str
    validation_error: str

class SniparaAPIError(ToolError):
    tool_name: str
    status_code: int | None
```

### Backend Errors

```python
class BackendError(RLMError): pass

class BackendConnectionError(BackendError):
    backend: str
    provider: str
    error: str

class BackendRateLimitError(BackendError):
    retry_after: int | None

class BackendAuthError(BackendError):
    provider: str
```

---

## Tool System

### Tool

```python
class Tool:
    name: str                          # Unique identifier
    description: str                   # Human-readable description
    parameters: dict                   # JSON Schema for parameters
    handler: Callable                  # Async function to execute
```

**Example:**
```python
tool = Tool(
    name="get_weather",
    description="Get current weather for a city",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name"}
        },
        "required": ["city"]
    },
    handler=get_weather,
)
```

### ToolRegistry

```python
class ToolRegistry:
    def register(self, tool: Tool) -> None
    def unregister(self, name: str) -> None
    def get(self, name: str) -> Tool | None
    def list_all(self) -> list[Tool]
    def list_names(self) -> list[str]
```

---

## CLI Commands

### snipara-sandbox run

```bash
snipara-sandbox run "Your prompt here" [OPTIONS]
```

**Options:**
- `--env, -e` - Environment: local, docker, wasm
- `--model, -m` - Model to use
- `--max-depth` - Maximum recursion depth
- `--token-budget` - Token budget
- `--verbose, -v` - Verbose output

**Example:**
```bash
snipara-sandbox run "Analyze data.csv" --env docker --max-depth 6
```

### snipara-sandbox init

```bash
snipara-sandbox init [OPTIONS]
```

Initialize Snipara Sandbox configuration.

**Options:**
- `--path` - Config file path
- `--env` - Default environment

### snipara-sandbox config show

```bash
snipara-sandbox config show [OPTIONS]
```

Show the effective runtime configuration.

**Options:**
- `--config, -c` - Config file path
- `--json` - Output machine-readable JSON

### snipara-sandbox logs

```bash
snipara-sandbox logs [TRAJECTORY_ID] [OPTIONS]
```

View trajectory logs.

**Options:**
- `--dir` - Log directory
- `--limit` - Number of entries to show

### snipara-sandbox visualize

```bash
snipara-sandbox visualize [OPTIONS]
```

Launch trajectory visualizer.

**Options:**
- `--dir` - Log directory
- `--port` - Web server port
- `--host` - Web server host

### snipara-sandbox mcp-serve

```bash
snipara-sandbox mcp-serve [OPTIONS]
```

Start MCP server for Claude Desktop/Code.

**Options:**
- `--port` - Server port
- `--host` - Server host

### snipara-sandbox doctor

```bash
snipara-sandbox doctor
```

Check system dependencies and configuration.

### snipara-sandbox version

```bash
snipara-sandbox version
```

Show version information.

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SNIPARA_SANDBOX_BACKEND` | LLM backend | `litellm` |
| `SNIPARA_SANDBOX_MODEL` | Model identifier | `gpt-4o-mini` |
| `SNIPARA_SANDBOX_TEMPERATURE` | Temperature | `0.0` |
| `SNIPARA_SANDBOX_LLM_API_KEY` | LLM backend API key | None |
| `SNIPARA_SANDBOX_ENVIRONMENT` | Execution env | `local` |
| `SNIPARA_SANDBOX_DOCKER_IMAGE` | Docker image | `python:3.11-slim` |
| `SNIPARA_SANDBOX_MAX_DEPTH` | Max depth | `4` |
| `SNIPARA_SANDBOX_TOKEN_BUDGET` | Token budget | `8000` |
| `SNIPARA_SANDBOX_VERBOSE` | Verbose logging | `false` |
| `SNIPARA_API_KEY` | Snipara key | None |
| `SNIPARA_PROJECT_SLUG` | Snipara project | None |
