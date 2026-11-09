# Snipara Sandbox Quickstart

Get started with Snipara Sandbox in 5 minutes.

## Installation

```bash
# Provider examples in this guide
pip install 'snipara-sandbox[inference]'

# Account-free deterministic validation and MCP, without inference
pip install 'snipara-sandbox[validation,mcp]'

# With Docker support (recommended for production)
pip install 'snipara-sandbox[inference,docker]'

# With MCP server (for Claude Desktop/Code)
pip install snipara-sandbox[mcp]

# With Snipara context optimization
pip install snipara-sandbox[snipara]

# With WebAssembly support (no Docker required)
pip install snipara-sandbox[wasm]

# With trajectory visualizer
pip install snipara-sandbox[visualizer]

# Everything
pip install snipara-sandbox[all]
```

## Setup

Sandbox 3.0 keeps provider dependencies optional. Pure validation/MCP users do not
need an LLM API key or `[inference]`; the model/agent examples below do. Existing
`[all]` installations retain their full capabilities.

### 1. Initialize Configuration

```bash
snipara-sandbox init
```

This creates `snipara-sandbox.toml` with default settings.

### 2. Set API Keys

```bash
# Set your LLM provider key
export OPENAI_API_KEY=sk-...
# or
export ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Verify Setup

```bash
snipara-sandbox doctor
```

## Basic Usage

### CLI

```bash
# Simple completion
snipara-sandbox run "What is 2 + 2?"

# With a specific model
snipara-sandbox run -m gpt-4o "Explain recursion"

# With Docker isolation
snipara-sandbox run --env docker "Parse data.csv and count rows"

# Verbose mode (shows execution details)
snipara-sandbox run -v "Find all Python files in this directory"
```

### Python API

```python
import asyncio
from snipara_sandbox import SniparaSandbox

async def main():
    sandbox = SniparaSandbox(model="gpt-4o-mini")

    result = await sandbox.completion("What is the capital of France?")
    print(result.response)
    print(f"Tokens used: {result.total_tokens}")

asyncio.run(main())
```

## Code Execution

Snipara Sandbox can execute Python code in a sandboxed environment:

```python
from snipara_sandbox import SniparaSandbox

async def main():
    sandbox = SniparaSandbox(environment="local")  # or "docker" for isolation

    result = await sandbox.completion(
        "Read data.csv and calculate the average of the 'price' column"
    )
    print(result.response)
```

The LLM will automatically use the `execute_code` tool when needed.

## Adding Snipara

For intelligent context retrieval:

```bash
# Install Snipara plugin
pip install snipara-mcp

# Set credentials
export SNIPARA_API_KEY=snp-...
export SNIPARA_PROJECT_SLUG=my-project
```

```python
from snipara_sandbox import SniparaSandbox

sandbox = SniparaSandbox(
    snipara_api_key="snp-...",
    snipara_project_slug="my-project",
)

# Now the LLM can query your documentation
result = await sandbox.completion("How does authentication work in this project?")
```

## Custom Tools

Add your own tools:

```python
from snipara_sandbox import SniparaSandbox, Tool

async def get_weather(city: str) -> dict:
    # Your implementation
    return {"city": city, "temp": 72, "condition": "sunny"}

weather_tool = Tool(
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

sandbox = SniparaSandbox(tools=[weather_tool])
result = await sandbox.completion("What's the weather in Paris?")
```

## Docker Isolation

For untrusted inputs, use Docker:

```bash
# Make sure Docker is running
docker info

# Run with Docker isolation
snipara-sandbox run --env docker "Process the uploaded file"
```

Or in Python:

```python
sandbox = SniparaSandbox(environment="docker")
```

## WebAssembly Isolation

For environments without Docker, use WebAssembly:

```bash
# Install WebAssembly support
pip install snipara-sandbox[wasm]

# Run with WebAssembly isolation
snipara-sandbox run --env wasm "Process the data"
```

Or in Python:

```python
sandbox = SniparaSandbox(environment="wasm")
```

The WebAssembly environment uses Pyodide to run Python in a sandboxed WebAssembly runtime, providing isolation without requiring Docker.

## Streaming Completions

For real-time output, use streaming:

```python
async def main():
    sandbox = SniparaSandbox(model="gpt-4o-mini")

    async for chunk in sandbox.stream("Write a haiku about coding"):
        print(chunk, end="", flush=True)
```

Note: Streaming is for simple completions. Tool-using completions use the standard `completion()` method.

## Viewing Logs

```bash
# List recent trajectories
snipara-sandbox logs

# View specific trajectory
snipara-sandbox logs abc123-def456

# JSON output for scripting
snipara-sandbox logs --json
```

## Trajectory Visualizer

Debug your completions with the web-based visualizer:

```bash
# Install visualizer dependencies
pip install snipara-sandbox[visualizer]

# Launch the dashboard
snipara-sandbox visualize

# Custom port and log directory
snipara-sandbox visualize --dir ./my-logs --port 8080
```

The visualizer shows:
- Execution tree of recursive calls
- Token usage charts
- Tool call distribution
- Detailed event inspection

## Next Steps

- Read the [Architecture Guide](architecture.md)
- Learn about [Configuration Options](configuration.md)
- Explore [Tool Development](tools.md)
- Set up [Snipara Integration](snipara.md)
