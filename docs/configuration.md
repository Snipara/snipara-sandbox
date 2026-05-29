# Configuration Guide

Snipara Sandbox can be configured via TOML files, environment variables, or programmatically.

## Configuration Priority

1. **Environment variables** (highest priority)
2. **snipara-sandbox.toml config file**
3. **Default values** (lowest priority)

## Quick Setup

```bash
# Initialize config in current directory
snipara-sandbox init

# Check your setup
snipara-sandbox doctor

# Inspect the effective configuration
snipara-sandbox config show
snipara-sandbox config show --json
```

## Configuration File

Create `snipara-sandbox.toml` in your project root:

```toml
[snipara_sandbox]
# LLM Backend
backend = "litellm"           # litellm, openai, or anthropic
model = "gpt-4o-mini"         # Model identifier
temperature = 0.0             # Sampling temperature (0.0 = deterministic)

# Execution Environment
environment = "local"         # local, docker, or wasm

# Recursion Limits
max_depth = 4                 # Maximum recursive depth
max_subcalls = 12             # Maximum total LLM calls
token_budget = 8000           # Token limit per completion
tool_budget = 20              # Maximum tool calls
timeout_seconds = 120         # Overall timeout

# Logging
log_dir = "./logs"            # Trajectory log directory
verbose = false               # Enable verbose output
log_level = "INFO"            # Logging level

# Docker Settings (when environment = "docker")
docker_image = "python:3.11-slim"
docker_cpus = 1.0
docker_memory = "512m"
docker_network_disabled = true
docker_mount_workspace = true
# docker_workspace_path = "."   # Optional override; defaults to the current directory
docker_workspace_setup = "none" # "tests-only" or "dev" build a cached workspace image
# docker_workspace_install_command = "python -m pip install -e \".[dev]\""
# docker_workspace_install_extras = ["fastapi", "pydantic-settings"] # scoped deps on top of the setup mode
docker_timeout = 30

# Snipara Integration (optional — or use OAuth via snipara-mcp-login)
snipara_api_key = "snp-..."
snipara_project_slug = "my-project"
snipara_base_url = "https://api.snipara.com/mcp"
memory_enabled = false            # Enable Tier 2 memory tools
```

## Environment Variables

All configuration options can be set via environment variables with the `SNIPARA_SANDBOX_` prefix. Legacy `RLM_` variables are still accepted for existing installations.

```bash
# Backend
export SNIPARA_SANDBOX_BACKEND=litellm
export SNIPARA_SANDBOX_MODEL=gpt-4o-mini
export SNIPARA_SANDBOX_TEMPERATURE=0.0

# Environment
export SNIPARA_SANDBOX_ENVIRONMENT=docker

# Limits
export SNIPARA_SANDBOX_MAX_DEPTH=4
export SNIPARA_SANDBOX_MAX_SUBCALLS=12
export SNIPARA_SANDBOX_TOKEN_BUDGET=8000
export SNIPARA_SANDBOX_TOOL_BUDGET=20
export SNIPARA_SANDBOX_TIMEOUT_SECONDS=120

# Logging
export SNIPARA_SANDBOX_LOG_DIR=./logs
export SNIPARA_SANDBOX_VERBOSE=false
export SNIPARA_SANDBOX_LOG_LEVEL=INFO

# Docker
export SNIPARA_SANDBOX_DOCKER_IMAGE=python:3.11-slim
export SNIPARA_SANDBOX_DOCKER_CPUS=1.0
export SNIPARA_SANDBOX_DOCKER_MEMORY=512m
export SNIPARA_SANDBOX_DOCKER_NETWORK_DISABLED=true
export SNIPARA_SANDBOX_DOCKER_MOUNT_WORKSPACE=true
export SNIPARA_SANDBOX_DOCKER_WORKSPACE_PATH=.
export SNIPARA_SANDBOX_DOCKER_WORKSPACE_SETUP=dev
export SNIPARA_SANDBOX_DOCKER_WORKSPACE_INSTALL_COMMAND='python -m pip install -e ".[dev]"'
export SNIPARA_SANDBOX_DOCKER_TIMEOUT=30

# Snipara service integration
export SNIPARA_API_KEY=snp-...
export SNIPARA_PROJECT_SLUG=my-project
export SNIPARA_BASE_URL=https://api.snipara.com/mcp  # override base URL
export SNIPARA_MEMORY_ENABLED=true                   # enable memory tools
```

## API Key Configuration

Set your LLM provider API keys:

```bash
# OpenAI
export OPENAI_API_KEY=sk-...

# Anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# Azure OpenAI
export AZURE_API_KEY=...
export AZURE_API_BASE=https://your-resource.openai.azure.com
export AZURE_API_VERSION=2024-02-15-preview

# Other providers (via LiteLLM)
# See: https://docs.litellm.ai/docs/providers
```

## Programmatic Configuration

```python
from snipara_sandbox import SniparaSandbox, SniparaSandboxConfig

# Direct parameters (override config file)
sandbox = SniparaSandbox(
    model="gpt-4o",
    environment="docker",
    verbose=True,
)

# Custom config object
config = SniparaSandboxConfig(
    model="claude-sonnet-4-20250514",
    environment="docker",
    max_depth=6,
    token_budget=16000,
    docker_memory="1g",
)

sandbox = SniparaSandbox(config=config)
```

## Configuration Options Reference

### Backend Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `backend` | str | `"litellm"` | LLM backend: `litellm`, `openai`, `anthropic` |
| `model` | str | `"gpt-4o-mini"` | Model identifier |
| `temperature` | float | `0.0` | Sampling temperature (0.0-2.0) |
| `api_key` | str | None | Provider API key (usually via env var) |

### Environment Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `environment` | str | `"local"` | REPL environment: `local`, `docker`, `wasm` |

### Recursion Limits

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `max_depth` | int | `4` | Maximum recursive depth |
| `max_subcalls` | int | `12` | Maximum total LLM calls |
| `token_budget` | int | `8000` | Token limit per completion |
| `tool_budget` | int | `20` | Maximum tool calls |
| `timeout_seconds` | int | `120` | Overall execution timeout |

### Docker Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `docker_image` | str | `"python:3.11-slim"` | Docker image to use |
| `docker_cpus` | float | `1.0` | CPU limit |
| `docker_memory` | str | `"512m"` | Memory limit |
| `docker_network_disabled` | bool | `true` | Disable network access |
| `docker_mount_workspace` | bool | `true` | Mount the workspace read-only at `/workspace` |
| `docker_workspace_path` | Path | `None` | Override the mounted host path (defaults to current directory) |
| `docker_workspace_setup` | str | `"none"` | `none`, `package`, `tests-only`, or `dev` workspace image preparation mode |
| `docker_workspace_install_command` | str | `None` | Explicit install command used when preparing a workspace image |
| `docker_workspace_install_extras` | list[str] | `[]` | Extra pip requirements installed on top of the setup mode (e.g. what `conftest.py` imports) |
| `docker_timeout` | int | `30` | Per-execution timeout |

### Prepared Workspace Images

When `docker_workspace_setup` is set to `package`, `tests-only`, or `dev`,
Snipara Sandbox builds a cached local Docker image from the workspace before
execution. This solves the main gap between an isolated container and real
repo-backed tests: dependencies are installed at image-build time, while
runtime execution still uses a read-only mount and
`docker_network_disabled = true`.

- `package` tries `python -m pip install -e .` for standard Python projects, or
  `python -m pip install -r requirements.txt` when only requirements files are present.
- `tests-only` installs `pytest` and `pytest-asyncio`, then exposes the mounted
  workspace via `PYTHONPATH=/workspace` so tests can import the repo without a
  full editable install.
- `dev` tries `python -m pip install -e ".[dev]"` first, then falls back to
  `requirements-dev.txt`, `requirements-test.txt`, or `requirements.txt + pytest`.
- The build context ignores the project's `.dockerignore` and uses a
  sandbox-owned filter instead, keeping `tests/`, `README.md`, and packaging
  metadata available for repo-backed test runs.
- Workspace-image build failures include the recent Docker build log lines to
  make dependency/setup errors easier to debug.
- Use `docker_workspace_install_command` when your project needs a custom setup command.
- Use `docker_workspace_install_extras` to add just the runtime dependencies your
  test collection needs on top of any mode — for example, when a `tests-only`
  build fails because `conftest.py` imports `fastapi` or `pydantic-settings` —
  without resorting to a full `dev` install.
- The runtime sets `PYTEST_ADDOPTS=-o cache_dir=/tmp/pytest-cache` (the `-o` ini
  override form, since pytest 9 removed the `--cache-dir` CLI flag).

### Logging Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `log_dir` | Path | `"./logs"` | Trajectory log directory |
| `verbose` | bool | `false` | Enable verbose output |
| `log_level` | str | `"INFO"` | Logging level |

### Snipara Integration

OAuth tokens (`~/.snipara/tokens.json`) are checked first. If unavailable,
`SNIPARA_API_KEY` env var and config values are used as fallback. See
[Snipara Integration](snipara.md) for full auth resolution order.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `snipara_api_key` | str | None | Snipara API key (or use `SNIPARA_API_KEY` env var) |
| `snipara_project_slug` | str | None | Project slug (or use `SNIPARA_PROJECT_SLUG` env var) |
| `snipara_base_url` | str | `"https://api.snipara.com/mcp"` | API base URL |
| `memory_enabled` | bool | `false` | Enable Tier 2 memory tools (remember/recall/memories/forget) |

## Per-Project Configuration

Create `snipara-sandbox.toml` in each project directory:

```
~/projects/
├── frontend/
│   └── snipara-sandbox.toml     # model = "gpt-4o-mini"
├── backend/
│   └── snipara-sandbox.toml     # model = "gpt-4o", environment = "docker"
└── ml-pipeline/
    └── snipara-sandbox.toml     # model = "claude-sonnet-4-20250514", max_depth = 8
```

Each project uses its own `snipara-sandbox.toml` configuration. The CLI automatically detects the config from the current directory.

## Model Selection Guide

| Use Case | Recommended Model | Notes |
|----------|-------------------|-------|
| Quick tasks | `gpt-4o-mini` | Fast, cheap, good for simple tasks |
| Complex analysis | `gpt-4o` | Better reasoning, higher cost |
| Code generation | `claude-sonnet-4-20250514` | Excellent for code tasks |
| Large context | `claude-3-opus-20240229` | 200K context window |

## Security Recommendations

1. **Use Docker for production** - Better isolation for untrusted inputs
2. **Disable network in containers** - `docker_network_disabled = true`
3. **Set resource limits** - Prevent resource exhaustion
4. **Use environment variables for secrets** - Don't commit API keys
5. **Review trajectory logs** - Audit tool usage

## Troubleshooting

### "API key not found"

```bash
# Check if key is set
echo $OPENAI_API_KEY

# Or use snipara-sandbox doctor
snipara-sandbox doctor
```

### "Docker not available"

```bash
# Check Docker daemon
docker info

# Install Docker support
pip install snipara-sandbox[docker]
```

### "Config file not found"

```bash
# Initialize config
snipara-sandbox init

# Or specify path
snipara-sandbox run --config /path/to/snipara-sandbox.toml "Your prompt"
```
