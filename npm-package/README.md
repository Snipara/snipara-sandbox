# @snipara/sandbox

TypeScript SDK for Snipara Sandbox - Sandboxed Python execution for AI agents.

## Installation

```bash
npm install @snipara/sandbox
```

## Usage

### Type Definitions

```typescript
import type {
  ExecutePythonParams,
  ExecutePythonReceipt,
  REPLResult,
  AgentRunParams,
  AgentResult,
  TrustLevel,
  ExecutionEnvironment,
  ExecutionProfile,
} from '@snipara/sandbox';

// Type-safe parameters for execute_python
const params: ExecutePythonParams = {
  code: 'print(2 + 2)',
  profile: 'default',
  environment: 'sandbox',
  session_id: 'my-session',
};
```

`execute_python` returns plain-text tool output plus a structured receipt with
the effective environment, backend, timeout, session id, and resource metrics.

### Constants & Utilities

```typescript
import {
  EXECUTION_PROFILES,
  ALLOWED_IMPORTS,
  BLOCKED_IMPORTS,
  MCP_TOOLS,
  isImportAllowed,
  getProfile,
} from '@snipara/sandbox';

// Check execution profiles
console.log(EXECUTION_PROFILES.analysis);
// { timeout: 120, memory: '2g', description: '...' }

// Check if import is allowed in sandbox
console.log(isImportAllowed('json'));      // true
console.log(isImportAllowed('subprocess')); // false

// Get MCP tool names
console.log(MCP_TOOLS.EXECUTE_PYTHON); // 'execute_python'
```

## Trust Levels

| Level | Description |
|-------|-------------|
| `sandboxed` | RestrictedPython, safe stdlib only (default) |
| `docker` | Docker container isolation |
| `local` | Full access to files, subprocess, network |

## Execution Environments

| Environment | Description |
|-------------|-------------|
| `sandbox` | RestrictedPython, safe stdlib only |
| `docker` | Docker container isolation |
| `local` | Full local access, gated by `trust_level='local'` |

## Execution Profiles

| Profile | Timeout | Memory | Use Case |
|---------|---------|--------|----------|
| `quick` | 5s | 128MB | Simple math, string ops |
| `default` | 30s | 512MB | Standard algorithms |
| `analysis` | 120s | 2GB | Large datasets |
| `extended` | 300s | 4GB | Batch processing |

## MCP Tools

| Tool | Description |
|------|-------------|
| `execute_python` | Run Python code in sandbox |
| `get_repl_context` | Get persistent variables |
| `set_repl_context` | Set persistent variables |
| `clear_repl_context` | Clear session state |
| `list_sessions` | List active sessions |
| `destroy_session` | Destroy a session |
| `snipara_agent_run` | Start autonomous agent |
| `snipara_agent_status` | Check agent status |
| `snipara_agent_cancel` | Cancel running agent |

## License

MIT

## Dependency readiness (Sandbox 2.4)

Repository validation requests accept optional `requirements` with
`python_modules`, `node_modules` and `commands` arrays. These assert capabilities
already installed in the selected image; they never install dependencies.
Use a runtime supporting these fields. Existing requests remain valid without
them. See the repository's `docs/validation-toolbox.md` for prepared tooling.
