# Autonomous Snipara Sandbox Agent (Phase 9) ✅

## Status: Implemented

Install `snipara-sandbox[inference]` to use the provider-backed agent commands in
Sandbox 3.0. Deterministic validation/MCP users can instead install
`snipara-sandbox[validation,mcp]` without the inference stack. Selecting an agent
command without the extra returns an installation instruction; it does not
install dependencies or fall back to another execution mode.

The Autonomous Snipara Sandbox Agent implements the full Recursive Language Model loop. The model explores documentation, writes and executes code, spawns sub-LLM calls, and terminates when it has enough information to answer.

This is the capstone feature that combines all prior capabilities:
- **REPL execution** (Phase 1) -- Run code in sandboxed environments
- **Snipara context** (existing) -- Retrieve optimized documentation
- **Sub-LLM Orchestration** (Phase 8) -- Recursive sub-calls with budget control
- **Cost tracking** (Phase 4) -- Full cost visibility across the agent loop

## The Snipara Sandbox Loop

```
┌────────────────────────────────────────────────────────────────┐
│  Snipara Sandbox Agent Loop                                                 │
│                                                                  │
│  1. OBSERVE                                                      │
│     - Load project context (Snipara snipara_context_query)          │
│     - Scan documentation structure                               │
│     - Review existing code                                       │
│                                                                  │
│  2. THINK                                                        │
│     - Analyze what information is needed                         │
│     - Plan which files/sections to explore                       │
│     - Decide: explore more? sub-LLM call? or terminate?         │
│                                                                  │
│  3. ACT                                                          │
│     - Execute code to analyze data                               │
│     - Query Snipara for specific context                         │
│     - Spawn sub-LLM for focused sub-problems                    │
│                                                                  │
│  4. TERMINATE                                                    │
│     - Call FINAL("answer text") to return natural language       │
│     - Call FINAL_VAR("var_name") to return computed result       │
│     - Agent loop ends, result returned to user                   │
│                                                                  │
│  Loop: 1 → 2 → 3 → 2 → 3 → ... → 4                            │
│  Max iterations: configurable (default: 10, hard limit: 50)     │
└────────────────────────────────────────────────────────────────┘
```

## Implementation

### Source Files

| File | Description |
|------|-------------|
| `src/rlm/agent/__init__.py` | Exports `AgentRunner`, `AgentConfig`, `AgentResult` |
| `src/rlm/agent/config.py` | `AgentConfig` dataclass with hard safety limit clamping |
| `src/rlm/agent/result.py` | `AgentResult` dataclass with `success` property |
| `src/rlm/agent/runner.py` | Main `AgentRunner` class with `run()`, `cancel()`, `status` |
| `src/rlm/agent/terminal.py` | `AgentState` + `FINAL`/`FINAL_VAR` tool definitions |
| `src/rlm/agent/prompts.py` | System prompt and iteration prompt builder |
| `src/rlm/agent/guardrails.py` | `check_iteration_allowed()` safety checks |
| `src/rlm/mcp/server.py` | MCP tools: `snipara_agent_run`, `snipara_agent_status`, `snipara_agent_cancel` |
| `src/rlm/cli/main.py` | `snipara-sandbox agent` CLI command with Rich output |
| `src/rlm/core/exceptions.py` | `AgentError`, `AgentIterationLimitExceeded`, `AgentCostLimitExceeded`, `AgentCancelled` |

### Test Files

| File | Tests | Coverage |
|------|-------|---------|
| `tests/unit/test_agent_runner.py` | 10 | Deterministic replay, budget enforcement, timeout, cancel, FINAL_VAR |
| `tests/unit/test_agent_terminal.py` | 5 | FINAL/FINAL_VAR tool handlers, AgentState |
| `tests/unit/test_agent_config.py` | 8 | Hard limit clamping, defaults |
| `tests/unit/test_agent_guardrails.py` | 5 | `check_iteration_allowed()` at each limit |
| `tests/unit/test_agent_prompts.py` | 8 | Prompt includes task, iteration, budget; warning on final |

## FINAL / FINAL_VAR Protocol

The agent signals completion using registered **tool calls** (not string parsing):

### `FINAL(answer)`

Returns a natural language answer. The agent has gathered enough context and is ready to synthesize.

```python
# The LLM calls this tool when ready to answer:
FINAL(answer="The authentication system uses JWT tokens with RS256 signing. "
             "Tokens are issued by /api/auth/login and refreshed via /api/auth/refresh.")
```

**Implementation** ([terminal.py](../src/rlm/agent/terminal.py)):
- Sets `AgentState.is_terminal = True`
- Stores `terminal_value = answer`
- Sets `terminal_type = "final"`
- Returns confirmation string to the LLM

### `FINAL_VAR(variable_name)`

Returns a computed value from the REPL context. Useful when the agent wrote code to analyze data.

```python
# The LLM executes code, then returns the computed result:
execute_python(code="result = sum(range(100))")
FINAL_VAR(variable_name="result")  # Returns 4950
```

**Implementation**:
- Reads variable from `repl.get_context()`
- Converts value to string via `str()`
- Sets `terminal_type = "final_var"`
- If variable not found: returns error, does NOT set terminal state (agent continues)

## AgentRunner

### Core Loop

```python
class AgentRunner:
    def __init__(self, sandbox: SniparaSandbox, config: AgentConfig | None = None):
        self.sandbox = sandbox
        self.config = config or AgentConfig()

    async def run(self, task: str) -> AgentResult:
        # 1. Register FINAL/FINAL_VAR terminal tools
        # 2. Optional: auto-load Snipara context for task
        # 3. Loop:
        #    a. Check cancellation flag
        #    b. Check guardrails (iteration, cost, token limits)
        #    c. Build iteration prompt with previous actions summary
        #    d. Force FINAL instruction on last iteration
        #    e. result = await sandbox.completion(prompt, system, options)
        #    f. Track tokens/cost, accumulate events
        #    g. Record tool call summaries for next iteration context
        #    h. if state.is_terminal: return AgentResult(success)
        # 4. Return forced termination result
        # 5. Finally: unregister terminal tools

    def cancel(self) -> None:
        """Set cancellation flag, checked at each iteration."""

    @property
    def status(self) -> dict:
        """Current run_id, iteration, tokens, cost, terminal state."""
```

### Key Design Decisions

1. **Each iteration = one `sandbox.completion()` call**: Context carried forward via previous_actions summary
2. **FINAL/FINAL_VAR are tools**: Registered in tool registry per agent run, cleaned up in `finally` block
3. **Auto-context on first iteration**: If Snipara is configured and `auto_context=True`, `snipara_context_query` is called with the task to inject relevant documentation into the system prompt
4. **Iteration budget slicing**: Each iteration gets `min(remaining, token_budget // max_iterations * 2)` tokens
5. **Previous actions**: Last 5 actions summarized as context for next iteration (prevents context explosion)

## AgentConfig

```python
from snipara_sandbox.agent.config import AgentConfig

# Hard safety limits (non-configurable, clamped in __post_init__)
ABSOLUTE_MAX_ITERATIONS = 50
ABSOLUTE_MAX_COST = 10.0
ABSOLUTE_MAX_TIMEOUT = 600
ABSOLUTE_MAX_DEPTH = 5

config = AgentConfig(
    max_iterations=10,      # Clamped to 50
    max_depth=3,            # Clamped to 5
    token_budget=50000,
    cost_limit=2.0,         # Clamped to $10
    timeout_seconds=120,    # Clamped to 600
    auto_context=True,      # Auto-load Snipara context on first iteration
    context_budget=8000,    # Tokens for auto-context query
    trajectory_log=True,    # Log full trajectory
    tool_budget=50,         # Tool calls across all iterations
)
```

**Clamping**: Values exceeding hard limits are silently clamped in `__post_init__`:
```python
config = AgentConfig(max_iterations=100)
assert config.max_iterations == 50  # Clamped to ABSOLUTE_MAX_ITERATIONS
```

## AgentResult

```python
from snipara_sandbox.agent.result import AgentResult

result = await runner.run("What is 2+2?")

result.answer           # "4" or "The answer is 4"
result.answer_source    # "final", "final_var", "forced", or "error"
result.iterations       # Number of iterations completed
result.total_tokens     # Total tokens used
result.total_cost       # Total cost in USD
result.duration_ms      # Wall-clock time
result.forced_termination  # True if limits were hit
result.run_id           # Unique run identifier
result.trajectory       # List of TrajectoryEvent
result.iteration_summaries  # Per-iteration stats (tokens, cost, tool_calls)
result.success          # True when answer_source in ("final", "final_var") and not forced
result.to_dict()        # JSON-serializable dict
```

## Guardrails

The `check_iteration_allowed()` function ([guardrails.py](../src/rlm/agent/guardrails.py)) is called before each iteration:

```python
def check_iteration_allowed(
    iteration: int,
    config: AgentConfig,
    total_cost: float,
    total_tokens: int,
) -> tuple[bool, str | None]:
    """Check if the next iteration should proceed.

    Returns (allowed, reason) where reason is None if allowed.
    """
```

**Checks**:
1. `iteration >= config.max_iterations` → "Iteration limit reached"
2. `total_cost >= config.cost_limit` → "Cost limit reached"
3. `total_tokens >= config.token_budget` → "Token budget exhausted"

### Graceful Degradation

When limits are hit, the agent doesn't crash:

| Scenario | Behavior |
|----------|----------|
| Budget exhausted | Loop exits, returns last action summary with `forced_termination=True` |
| Iteration limit | Loop exits, returns last action summary with `forced_termination=True` |
| Timeout | `asyncio.TimeoutError` caught, returns error result |
| Cancellation | `_cancelled` flag checked each iteration, returns error result |
| Final iteration | Prompt includes `**WARNING: This is your FINAL iteration. You MUST call FINAL...**` |

## Prompts

### System Prompt

The `AGENT_SYSTEM_PROMPT` ([prompts.py](../src/rlm/agent/prompts.py)) instructs the LLM on:
- Available tools (explore, analyze, delegate, terminate)
- When to use FINAL vs FINAL_VAR
- Budget awareness
- Snipara tool names (snipara_context_query, snipara_remember)

### Iteration Prompt

`build_iteration_prompt()` constructs per-iteration context:

```python
prompt = build_iteration_prompt(
    task="What is 2+2?",
    iteration=2,                       # 0-based
    max_iterations=10,
    previous_actions=["Did step 1"],   # Last 5 actions included
    remaining_budget=48000,
)
```

Output includes:
- Task description
- Iteration counter: "Iteration 3/10"
- Previous actions summary (last 5)
- Remaining token budget
- **WARNING on final iteration**: Forces FINAL call

## CLI Usage

```bash
# Basic agent run
snipara-sandbox agent "What is 2+2?"

# With all options
snipara-sandbox agent "Explain the auth system" \
    --model claude-sonnet-4-20250514 \
    --backend litellm \
    --env docker \
    --max-iterations 20 \
    --budget 50000 \
    --cost-limit 5.0 \
    --timeout 300 \
    --auto-context \
    --verbose

# JSON output for scripting
snipara-sandbox agent "Count lines in main.py" --json

# Disable auto-context
snipara-sandbox agent "Simple math" --no-auto-context
```

### CLI Output

The CLI uses Rich for formatted output:

**Standard output**:
```
╭─ Answer ─────────────────────────────────╮
│ The answer is 4.                          │
╰──────────────────────────────────────────╯

     Agent Summary
┌────────────────┬──────────────┐
│ Metric         │ Value        │
├────────────────┼──────────────┤
│ Run ID         │ a1b2c3d4     │
│ Success        │ Yes          │
│ Source         │ final        │
│ Iterations     │ 2            │
│ Total Tokens   │ 3,247        │
│ Total Cost     │ $0.0162      │
│ Duration       │ 4,523ms      │
└────────────────┴──────────────┘
```

**Verbose output** (adds iteration details table):
```
     Iteration Details
┌───┬────────┬─────────┬───────┬──────────────────────────┐
│ # │ Tokens │ Cost    │ Tools │ Preview                  │
├───┼────────┼─────────┼───────┼──────────────────────────┤
│ 1 │ 1847   │ $0.0092 │ 2     │ Used execute_python...   │
│ 2 │ 1400   │ $0.0070 │ 1     │ Called FINAL with...     │
└───┴────────┴─────────┴───────┴──────────────────────────┘
```

## MCP Tools

Three MCP tools are added to `src/rlm/mcp/server.py` for Claude Desktop/Code integration:

### `snipara_agent_run`

Start an autonomous agent as an async task.

```python
# Parameters
{
    "task": "string (required) - The task to solve",
    "max_iterations": "integer (default: 10, max: 50)",
    "token_budget": "integer (default: 50000)",
    "cost_limit": "number (default: 2.0, max: 10.0)"
}

# Returns
{
    "run_id": "a1b2c3d4",
    "status": "running",
    "task": "What is 2+2?",
    "config": {"max_iterations": 10, "token_budget": 50000, "cost_limit": 2.0}
}
```

### `snipara_agent_status`

Check the status of a running or completed agent.

```python
# Parameters
{"run_id": "a1b2c3d4"}

# Returns (running)
{"run_id": "a1b2c3d4", "status": "running", "elapsed_seconds": 12}

# Returns (completed)
{
    "run_id": "a1b2c3d4",
    "status": "completed",
    "result": {
        "answer": "The answer is 4",
        "answer_source": "final",
        "iterations": 2,
        "total_tokens": 3247,
        "total_cost": 0.0162,
        "success": true
    },
    "elapsed_seconds": 4
}
```

### `snipara_agent_cancel`

Cancel a running agent.

```python
# Parameters
{"run_id": "a1b2c3d4"}

# Returns
"Agent run 'a1b2c3d4' cancelled"
```

### AgentManager

The `AgentManager` class tracks running agents by `run_id`, similar to `SessionManager` for REPL sessions:

```python
class AgentManager:
    def start(run_id, task, coro) -> AgentRun  # Starts as asyncio.Task
    def get(run_id) -> AgentRun | None         # Get by ID
    def cancel(run_id) -> bool                 # Cancel running agent
    def list_runs() -> list[dict]              # List all with status
```

## Python API

```python
from snipara_sandbox import SniparaSandbox
from snipara_sandbox.agent import AgentRunner, AgentConfig

# Create Snipara Sandbox instance
sandbox = SniparaSandbox(
    model="claude-sonnet-4-20250514",
    environment="docker",
)

# Configure agent
config = AgentConfig(
    max_iterations=10,
    token_budget=50000,
    cost_limit=2.0,
    auto_context=True,
)

# Run agent
runner = AgentRunner(sandbox, config)
result = await runner.run(
    "How does the payment webhook handler validate Stripe signatures?"
)

print(result.answer)
print(f"Success: {result.success}")
print(f"Iterations: {result.iterations}")
print(f"Cost: ${result.total_cost:.4f}")
print(f"Tokens: {result.total_tokens:,}")

# Cancel a running agent
runner.cancel()

# Check status mid-run
print(runner.status)
```

## Tool Set Available to Agent

Each iteration, the agent has access to all registered tools:

| Tool | Category | Purpose |
|------|----------|---------|
| `execute_python` | REPL | Run code in sandbox |
| `get_repl_context` | REPL | Read persistent variables |
| `set_repl_context` | REPL | Write persistent variables |
| `snipara_context_query` | Snipara | Semantic documentation search |
| `snipara_search` | Snipara | Regex documentation search |
| `snipara_shared_context` | Snipara | Team guidelines and best practices |
| `snipara_remember` | Snipara | Store memories (when `memory_enabled`) |
| `snipara_recall` | Snipara | Recall memories (when `memory_enabled`) |
| `snipara_sub_complete` | Sub-LLM | Delegate focused sub-problems |
| `snipara_batch_complete` | Sub-LLM | Parallel sub-queries |
| **`FINAL`** | Terminal | Return natural language answer |
| **`FINAL_VAR`** | Terminal | Return computed REPL variable |

## Exception Hierarchy

```python
from snipara_sandbox.core.exceptions import (
    AgentError,                    # Base agent exception
    AgentIterationLimitExceeded,   # Iteration limit hit
    AgentCostLimitExceeded,        # Cost limit hit
    AgentCancelled,                # Agent was cancelled
)
```

## Safety Summary

### Hard Limits (Non-Configurable)

| Limit | Value | Rationale |
|-------|-------|-----------|
| Max iterations | 50 | Prevent infinite loops |
| Max depth | 5 | Prevent recursion bombs |
| Max cost | $10.00 | Prevent billing surprises |
| Max timeout | 600s | Prevent hung agents |

### Configurable Limits

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `max_iterations` | 10 | 1-50 | Observe-think-act cycles |
| `max_depth` | 3 | 1-5 | Sub-LLM recursion depth |
| `token_budget` | 50,000 | any | Total tokens across all calls |
| `cost_limit` | $2.00 | $0-$10 | Dollar cap for entire run |
| `timeout_seconds` | 120 | 1-600 | Wall-clock timeout |
| `tool_budget` | 50 | any | Tool calls across all iterations |

## Testing

39 tests across 5 test files ensure agent reliability:

### Test Categories

**Runner tests** (`test_agent_runner.py`):
- Deterministic replay with mocked backend returning tool calls then FINAL
- Budget enforcement with low cost_limit → forced termination
- Iteration limit enforcement → forced termination
- Timeout handling → forced termination
- Cancel midway → forced termination
- FINAL_VAR reads variable from mock REPL context
- Auto-context injection when Snipara tool available
- No auto-context when disabled or Snipara unavailable
- Multiple iterations with tool usage tracking
- Forced termination includes last action summary

**Terminal tool tests** (`test_agent_terminal.py`):
- FINAL sets AgentState correctly
- FINAL_VAR reads from REPL context
- FINAL_VAR with missing variable returns error, doesn't terminate
- Two tools created with correct names

**Config tests** (`test_agent_config.py`):
- Default values correct
- Hard limit clamping for all four limits
- Values within limits not clamped
- Hard limit constants correct

**Guardrail tests** (`test_agent_guardrails.py`):
- Allowed at start
- Blocked at each limit (iteration, cost, token)
- Allowed just under all limits

**Prompt tests** (`test_agent_prompts.py`):
- System prompt contains key instructions
- Iteration prompt includes task, iteration count, budget
- Previous actions included in prompt
- Warning on final iteration
- No warning before final
- Previous actions limited to last 5
