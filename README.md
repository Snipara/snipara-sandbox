# Snipara Sandbox

## Stateful execution runtime for persistent AI workflows

Modern AI agents can reason, write code, call tools, and solve complex tasks.

But most agent workflows are still transient. They lose execution state, restart
from partial context, repeat failed work, and struggle to resume reliably across
sessions, tools, users, and model changes.

Snipara Sandbox is a state-aware execution runtime for persistent AI workflows. It
adds a durable execution layer around AI agents so they can execute code safely,
preserve trajectories, validate intermediate results, and resume long-running
workflows with continuity.

```text
Context
  -> Planning
  -> Execution
  -> Validation
  -> Persistent State
  -> Resume Later
```

Snipara Sandbox is part of the broader Snipara ecosystem. Snipara provides persistent
project memory and shared context. Snipara Sandbox adds persistent execution,
resumable workflows, and sandboxed task continuity.

## The Problem

Large context windows are useful, but they do not solve execution continuity.

AI systems also need infrastructure for:

- persistent execution state
- resumable workflows
- sandboxed code execution
- validation-aware task loops
- trajectory persistence
- recoverable orchestration

Without that layer, even strong agents often rediscover prior results, lose
intermediate state, and restart work instead of continuing it.

Snipara Sandbox exists to make execution durable.

## Core Concepts

### Persistent execution state

Snipara Sandbox gives workflows a stateful execution environment instead of treating
every task as a one-shot prompt. Agents can store intermediate results, inspect
prior execution, and continue from known state.

### Sandboxed execution

Agents can execute Python in controlled environments:

- local RestrictedPython execution for fast trusted development
- Docker isolation for stronger process and filesystem boundaries
- optional WebAssembly support for portable sandboxing

### Trajectory persistence

Executions can be logged as structured trajectories. This makes workflows easier
to inspect, debug, audit, replay, and improve.

### Validation-aware orchestration

Snipara Sandbox is designed for workflows that do not just generate outputs, but also
check them. Code execution, tool calls, and validation steps become part of the
same recoverable workflow.

## Features

- Persistent workflow state
- Sandboxed Python execution
- Docker isolation with resource limits
- Resumable task orchestration
- Trajectory logging
- Validation-aware execution loops
- Multi-step execution continuity
- MCP-compatible runtime tools
- Optional Snipara context and memory integration
- LiteLLM backend support for OpenAI, Anthropic, and 100+ providers

## Relationship With Snipara

Snipara Sandbox is designed to work naturally with Snipara.

```text
LLM / Agent
  -> Snipara Context and Memory
  -> Snipara Sandbox
  -> Sandboxed Execution
```

In this architecture:

- Snipara provides persistent shared context, project memory, and retrieval.
- Snipara Sandbox provides persistent execution continuity and sandboxed runtime state.
- The LLM handles reasoning, generation, and tool selection.

The runtime is optional. Snipara can function independently as a shared memory
and context layer for AI systems.

Snipara Sandbox extends the stack when a workflow needs:

- long-running task execution
- state continuity across sessions
- validation through real code
- auditable execution traces
- recoverable orchestration

In short: Snipara keeps the context alive. Snipara Sandbox keeps the execution alive.

## Installation

```bash
# Basic install
pip install snipara-sandbox

# With Docker sandbox support
pip install snipara-sandbox[docker]

# With MCP server support
pip install snipara-sandbox[mcp]

# With Snipara integration
pip install snipara-sandbox[snipara]

# Full install
pip install snipara-sandbox[all]
```

Package version in this repo: `2.2.6`

See [CHANGELOG.md](CHANGELOG.md) for release history.

## Quick Start

### CLI

```bash
snipara-sandbox init

snipara-sandbox run "Analyze this dataset and validate the result with Python"

snipara-sandbox run --env docker "Process this untrusted input in an isolated runtime"

snipara-sandbox logs
```

Inspect the effective configuration:

```bash
snipara-sandbox config show
snipara-sandbox config show --json
```

The legacy `rlm` command remains available for existing users.

### Python API

```python
import asyncio

from snipara_sandbox import SniparaSandbox


async def main():
    runtime = SniparaSandbox(
        model="gpt-4o-mini",
        environment="docker",
        max_depth=4,
        token_budget=8000,
    )

    result = await runtime.completion(
        "Find the data quality issues in ./data and validate the findings."
    )

    print(result.response)
    print(result.trajectory_id)


asyncio.run(main())
```

## MCP Runtime Tools

Snipara Sandbox includes an MCP server that gives AI coding agents a sandboxed Python
runtime without requiring separate LLM API keys for the runtime itself.

```bash
pip install snipara-sandbox[mcp]
snipara-sandbox mcp-serve
```

Example MCP configuration:

```json
{
  "mcpServers": {
    "SniparaSandbox": {
      "command": "snipara-sandbox",
      "args": ["mcp-serve"]
    }
  }
}
```

Core MCP tools:

| Tool | Purpose |
|------|---------|
| `execute_python` | Execute Python in a sandboxed session |
| `get_repl_context` | Read persistent session variables |
| `set_repl_context` | Store persistent session variables |
| `clear_repl_context` | Reset session state |
| `list_sessions` | Inspect active runtime sessions |
| `destroy_session` | Destroy a runtime session |
| `snipara_agent_run` | Start an autonomous agent task |
| `snipara_agent_status` | Check an agent run |
| `snipara_agent_cancel` | Cancel an agent run |

## Using Snipara Sandbox With Snipara

Snipara credentials are detected automatically when available. The native HTTP
client is preferred, with `snipara-mcp` kept as a compatibility fallback.

OAuth:

```bash
snipara-mcp-login
snipara-mcp-status
```

API key:

```bash
export SNIPARA_API_KEY=snp-...
export SNIPARA_PROJECT_SLUG=my-project
```

Python:

```python
from snipara_sandbox import SniparaSandbox

runtime = SniparaSandbox(
    model="gpt-4o-mini",
    environment="docker",
    snipara_api_key="snp-...",
    snipara_project_slug="my-project",
)

result = await runtime.completion(
    "Use project context to explain the authentication flow and validate examples."
)
```

When configured, Snipara tools provide semantic context retrieval, shared project
context, and optional durable memory. Snipara Sandbox uses that context during
execution while preserving the workflow trajectory.

## Configuration

Create `snipara-sandbox.toml` in your project:

```toml
[snipara_sandbox]
backend = "litellm"
model = "gpt-4o-mini"
environment = "docker"
max_depth = 4
max_subcalls = 12
token_budget = 8000
verbose = false

docker_image = "python:3.11-slim"
docker_cpus = 1.0
docker_memory = "512m"
docker_network_disabled = true
docker_mount_workspace = true
# docker_workspace_path = "."
docker_workspace_setup = "none"  # use "tests-only" or "dev" for repo-backed test runs
# docker_workspace_install_command = "python -m pip install -e \".[dev]\""

snipara_project_slug = "your-project"
```

Environment variables are also supported:

```bash
export SNIPARA_SANDBOX_MODEL=gpt-4o-mini
export SNIPARA_SANDBOX_ENVIRONMENT=docker
export SNIPARA_PROJECT_SLUG=my-project
```

## Runtime Environments

| Environment | Use case | Isolation |
|-------------|----------|-----------|
| `local` | Fast trusted development | RestrictedPython in process |
| `docker` | Production and untrusted inputs | Container isolation |
| `wasm` | Portable sandboxing | WebAssembly runtime |

Docker mode is recommended for production and untrusted execution.
By default it mounts the current workspace read-only at `/workspace`, so the
runtime can inspect and test the repo while keeping process isolation and
network disabled.

For actual repo-backed test runs, enable workspace setup:

```toml
[snipara_sandbox]
environment = "docker"
docker_mount_workspace = true
docker_workspace_setup = "tests-only"
```

That prepares a cached local Docker image from the current workspace and
installs its dependencies at image-build time. Runtime execution still uses a
read-only mount and no network. `tests-only` installs `pytest` and
`pytest-asyncio` while exposing the mounted repo via `PYTHONPATH=/workspace`;
`dev` performs a full editable dev install instead. The workspace-image build
uses a sandbox-owned context instead of the project's `.dockerignore`, so test
files and packaging metadata remain available, and build failures now include
recent Docker log lines for faster debugging. If your project does not use
`.[dev]`, set `docker_workspace_install_command` explicitly.

```bash
snipara-sandbox run --env docker "Validate this user-submitted transformation"
```

## Trajectory Logging

Snipara Sandbox records execution trajectories as structured JSONL logs.

```bash
snipara-sandbox logs
snipara-sandbox logs <trajectory-id>
```

Trajectory events include:

- prompts and responses
- tool calls and tool results
- parent and child call relationships
- token usage
- duration and execution metadata

For visual inspection:

```bash
pip install snipara-sandbox[visualizer]
snipara-sandbox visualize
```

## Development

```bash
git clone https://github.com/Snipara/snipara-sandbox
cd snipara-sandbox

pip install -e ".[dev]"

pytest
ruff check src/
mypy src/
python -m build
```

## Documentation

- [Quickstart Guide](docs/quickstart.md)
- [Architecture Guide](docs/architecture.md)
- [MCP Integration](docs/mcp-integration.md)
- [Configuration](docs/configuration.md)
- [Tool Development](docs/tools.md)
- [Snipara Integration](docs/snipara.md)

## Repositories

- [Snipara Server](https://github.com/Snipara/snipara-server)
- [Snipara MCP](https://github.com/alopez3006/snipara-mcp)
- [Snipara Memory](https://github.com/Snipara/snipara-memory)
- [Snipara Sandbox](https://github.com/Snipara/snipara-sandbox)

## License

Apache 2.0. See [LICENSE](LICENSE).
