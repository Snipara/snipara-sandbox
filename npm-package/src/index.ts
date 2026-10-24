/**
 * Snipara Sandbox TypeScript SDK
 *
 * Provides types and utilities for working with Snipara Sandbox MCP tools.
 *
 * @example
 * ```typescript
 * import type { ExecutePythonParams, REPLResult } from '@snipara/sandbox';
 *
 * const params: ExecutePythonParams = {
 *   code: 'print(2 + 2)',
 *   profile: 'default',
 * };
 * ```
 */

// ============================================================================
// Execution Profiles
// ============================================================================

/**
 * Execution profile names for resource limits.
 */
export type ExecutionProfile = 'quick' | 'default' | 'analysis' | 'extended';

/**
 * Execution environment for MCP-backed Python runs.
 *
 * - `sandbox`: RestrictedPython, safe stdlib only
 * - `docker`: Docker container isolation
 * - `local`: Full local access, gated by trust_level='local'
 */
export type ExecutionEnvironment = 'sandbox' | 'docker' | 'local';

/**
 * Profile configurations with timeout and memory limits.
 */
export const EXECUTION_PROFILES: Record<ExecutionProfile, { timeout: number; memory: string; description: string }> = {
  quick: {
    timeout: 5,
    memory: '128m',
    description: 'Fast operations: simple math, string manipulation',
  },
  default: {
    timeout: 30,
    memory: '512m',
    description: 'Standard operations: data processing, algorithms',
  },
  analysis: {
    timeout: 120,
    memory: '2g',
    description: 'Heavy computation: large datasets, complex algorithms',
  },
  extended: {
    timeout: 300,
    memory: '4g',
    description: 'Long-running tasks: batch processing, extensive analysis',
  },
};

// ============================================================================
// Trust Levels
// ============================================================================

/**
 * Trust level for code execution.
 *
 * - `sandboxed`: RestrictedPython, safe stdlib only (default)
 * - `docker`: Docker container isolation
 * - `local`: Full access to files, subprocess, network, any library
 */
export type TrustLevel = 'sandboxed' | 'docker' | 'local';

// ============================================================================
// MCP Tool Types
// ============================================================================

/**
 * Parameters for execute_python MCP tool.
 */
export interface ExecutePythonParams {
  /** Python code to execute */
  code: string;
  /** Timeout in seconds (overrides profile). Max: 300 */
  timeout?: number;
  /** Execution profile: quick (5s), default (30s), analysis (120s), extended (300s) */
  profile?: ExecutionProfile;
  /** Execution environment. Omit to use the server config default. */
  environment?: ExecutionEnvironment;
  /** Session ID for isolated context (default: 'default') */
  session_id?: string;
}

/**
 * Parameters for get_repl_context MCP tool.
 */
export interface GetREPLContextParams {
  /** Session ID (default: 'default') */
  session_id?: string;
}

/**
 * Parameters for set_repl_context MCP tool.
 */
export interface SetREPLContextParams {
  /** Variable name */
  key: string;
  /** JSON-encoded value to store */
  value: string;
  /** Session ID (default: 'default') */
  session_id?: string;
}

/**
 * Parameters for clear_repl_context MCP tool.
 */
export interface ClearREPLContextParams {
  /** Session ID (default: 'default') */
  session_id?: string;
}

/**
 * Parameters for destroy_session MCP tool.
 */
export interface DestroySessionParams {
  /** Session ID to destroy */
  session_id: string;
}

/**
 * Parameters for snipara_agent_run MCP tool.
 */
export interface AgentRunParams {
  /** The task for the agent to solve */
  task: string;
  /** Maximum iterations (default: 10, max: 50) */
  max_iterations?: number;
  /** Token budget (default: 50000) */
  token_budget?: number;
  /** Cost limit in USD (default: 2.0, max: 10.0) */
  cost_limit?: number;
}

/**
 * Parameters for snipara_agent_status MCP tool.
 */
export interface AgentStatusParams {
  /** The agent run ID from snipara_agent_run */
  run_id: string;
}

/**
 * Parameters for snipara_agent_cancel MCP tool.
 */
export interface AgentCancelParams {
  /** The agent run ID to cancel */
  run_id: string;
}

// ============================================================================
// Result Types
// ============================================================================

/**
 * Result from Python code execution.
 */
export interface REPLResult {
  /** Standard output from execution */
  output: string;
  /** Error message if execution failed */
  error: string | null;
  /** Execution time in milliseconds */
  execution_time_ms: number;
  /** Whether output was truncated */
  truncated: boolean;
  /** Peak memory usage in bytes (Unix only) */
  memory_peak_bytes: number | null;
  /** CPU time in milliseconds (Unix only) */
  cpu_time_ms: number | null;
  /** Whether execution succeeded */
  success: boolean;
}

/**
 * Structured receipt returned by the execute_python MCP tool.
 */
export interface ExecutePythonReceipt {
  /** Effective execution environment */
  environment: ExecutionEnvironment;
  /** REPL implementation class name */
  repl_backend: string;
  /** Session trust level */
  trust_level: TrustLevel;
  /** Execution profile applied to the run */
  profile: ExecutionProfile | string;
  /** Effective timeout in seconds */
  timeout_seconds: number;
  /** Session identifier */
  session_id: string;
  /** Whether execution succeeded */
  success: boolean;
  /** Wall-clock execution time */
  execution_time_ms: number;
  /** CPU time consumed, when available */
  cpu_time_ms: number | null;
  /** Peak memory usage, when available */
  memory_peak_bytes: number | null;
  /** Whether tool output was truncated */
  truncated: boolean;
  /** Error message for failed runs */
  error: string | null;
}

/**
 * Session information.
 */
export interface SessionInfo {
  /** Session ID */
  id: string;
  /** Bound execution environment */
  environment: ExecutionEnvironment | string;
  /** Trust level of the session */
  trust_level: TrustLevel;
  /** Creation timestamp */
  created_at: number;
  /** Last access timestamp */
  last_access: number;
  /** Age in seconds */
  age_seconds: number;
  /** Idle time in seconds */
  idle_seconds: number;
  /** Names of variables in context */
  context_keys: string[];
}

/**
 * Agent run status.
 */
export type AgentStatus = 'running' | 'completed' | 'error' | 'cancelled';

/**
 * Result from agent run.
 */
export interface AgentResult {
  /** The final answer from the agent */
  answer: string;
  /** Source of the answer: 'final' | 'final_var' | 'forced' */
  answer_source: string;
  /** Number of iterations completed */
  iterations: number;
  /** Total tokens used */
  total_tokens: number;
  /** Total cost in USD */
  total_cost: number;
  /** Duration in milliseconds */
  duration_ms: number;
  /** Whether termination was forced due to limits */
  forced_termination: boolean;
  /** Run ID */
  run_id: string;
  /** Whether the agent succeeded */
  success: boolean;
}

/**
 * Agent run response from snipara_agent_run.
 */
export interface AgentRunResponse {
  /** Run ID for checking status */
  run_id: string;
  /** Current status */
  status: AgentStatus;
  /** Task being executed */
  task: string;
  /** Configuration used */
  config: {
    max_iterations: number;
    token_budget: number;
    cost_limit: number;
  };
}

/**
 * Agent status response from snipara_agent_status.
 */
export interface AgentStatusResponse {
  /** Run ID */
  run_id: string;
  /** Current status */
  status: AgentStatus;
  /** Result if completed */
  result?: AgentResult;
  /** Error message if failed */
  error?: string;
  /** Elapsed time in seconds */
  elapsed_seconds: number;
}

// ============================================================================
// MCP Tool Names
// ============================================================================

/**
 * Available MCP tool names.
 */
export const MCP_TOOLS = {
  EXECUTE_PYTHON: 'execute_python',
  GET_REPL_CONTEXT: 'get_repl_context',
  SET_REPL_CONTEXT: 'set_repl_context',
  CLEAR_REPL_CONTEXT: 'clear_repl_context',
  LIST_SESSIONS: 'list_sessions',
  DESTROY_SESSION: 'destroy_session',
  AGENT_RUN: 'snipara_agent_run',
  AGENT_STATUS: 'snipara_agent_status',
  AGENT_CANCEL: 'snipara_agent_cancel',
} as const;

export type MCPToolName = typeof MCP_TOOLS[keyof typeof MCP_TOOLS];

// ============================================================================
// Configuration Types
// ============================================================================

/**
 * Snipara Sandbox configuration.
 */
export interface SniparaSandboxConfig {
  /** Backend: litellm | openai | anthropic */
  backend: string;
  /** Model name */
  model: string;
  /** Temperature for LLM calls */
  temperature: number;
  /** Trust level: sandboxed | docker | local */
  trust_level: TrustLevel;
  /** Token budget */
  token_budget: number;
  /** Maximum recursion depth */
  max_depth: number;
  /** Timeout in seconds */
  timeout_seconds: number;
  /** Whether memory tools are enabled */
  memory_enabled: boolean;
}

/** Legacy alias kept for existing TypeScript consumers. */
export type RLMConfig = SniparaSandboxConfig;

/**
 * Default configuration values.
 */
export const DEFAULT_CONFIG: SniparaSandboxConfig = {
  backend: 'litellm',
  model: 'gpt-4o-mini',
  temperature: 0.0,
  trust_level: 'sandboxed',
  token_budget: 50000,
  max_depth: 6,
  timeout_seconds: 300,
  memory_enabled: false,
};

// ============================================================================
// Allowed/Blocked Imports (for sandboxed mode)
// ============================================================================

/**
 * Modules allowed in sandboxed mode.
 */
export const ALLOWED_IMPORTS = [
  'json', 're', 'math', 'datetime', 'time', 'uuid',
  'hashlib', 'base64', 'string', 'textwrap',
  'collections', 'itertools', 'functools', 'operator',
  'dataclasses', 'typing', 'enum', 'copy',
  'csv', 'statistics', 'decimal', 'fractions',
  'pathlib', 'posixpath', 'ntpath',
  'urllib.parse',
  'difflib', 'unicodedata',
] as const;

/**
 * Modules blocked in sandboxed mode.
 */
export const BLOCKED_IMPORTS = [
  'os', 'sys', 'subprocess', 'shutil', 'platform', 'signal', 'resource',
  'socket', 'ssl', 'requests', 'urllib.request', 'http',
  'pickle', 'shelve', 'marshal',
  'sqlite3',
  'ctypes', 'cffi', 'mmap',
  'multiprocessing', 'threading', 'concurrent', 'asyncio',
  'importlib', 'builtins', 'eval', 'exec', 'compile', 'code',
  'tempfile', 'fileinput', 'glob', 'fnmatch',
  'pdb', 'bdb', 'trace', 'traceback', 'inspect', 'dis', 'ast',
  'atexit', 'gc',
] as const;

// ============================================================================
// Utility Functions
// ============================================================================

/**
 * Check if a module is allowed in sandboxed mode.
 */
export function isImportAllowed(moduleName: string): boolean {
  if (BLOCKED_IMPORTS.includes(moduleName as any)) {
    return false;
  }
  if (ALLOWED_IMPORTS.includes(moduleName as any)) {
    return true;
  }
  // Check parent modules
  const parts = moduleName.split('.');
  for (let i = 0; i < parts.length; i++) {
    const parent = parts.slice(0, i + 1).join('.');
    if (BLOCKED_IMPORTS.includes(parent as any)) {
      return false;
    }
    if (ALLOWED_IMPORTS.includes(parent as any)) {
      return true;
    }
  }
  return false;
}

/**
 * Get profile configuration by name.
 */
export function getProfile(name: ExecutionProfile) {
  return EXECUTION_PROFILES[name];
}

/** Deterministic Docker validation; standalone and independent of an LLM/account. */
export interface RepositoryValidationRequest {
  schema_version?: 'snipara.sandbox.validation.v1';
  workspace: string;
  argv: string[];
  image: string;
  profile?: 'python' | 'node' | 'mixed';
  cwd?: string;
  timeout_seconds?: number;
  cpus?: number;
  memory_mb?: number;
  pids?: number;
  disk_mb?: number;
  artifacts?: string[];
  /** Already-installed image capabilities; checked offline before the command. */
  requirements?: {
    python_modules?: string[];
    node_modules?: string[];
    commands?: string[];
  };
}
