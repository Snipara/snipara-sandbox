# Repository validation (candidate 2.3.0)

Sandbox remains standalone. No Snipara account, provider key, LLM inference,
Companion or Orchestrator is needed for these deterministic commands.
Install the Docker extra (`snipara-sandbox[docker]`) and select a prepared image.
No command below downloads an image, installs project dependencies, or falls
back to execution on the host. The image must include the project's dependencies.
Use immutable image references in release requests; receipts resolve the local
image ID even when a mutable tag was supplied.

Create a request JSON with an absolute workspace:

```json
{
  "workspace": "/absolute/path/to/project",
  "argv": ["node", "--test", "test/example.test.js"],
  "image": "your-reviewed-node-image@sha256:...",
  "profile": "node",
  "timeout_seconds": 120,
  "artifacts": []
}
```

Profiles: `python` requires Python, `node` requires Node/npm, `mixed` requires
both. A `pnpm` command also requires pnpm. Version probes run in the isolated
job and their output is recorded. Dependencies and lockfiles are source inputs;
prepare an image explicitly from reviewed lockfiles before testing. A missing
dependency is not silently installed. `docker/Dockerfile.validation` is a base
recipe for mixed projects, not a claim to contain every project's dependencies.

```sh
snipara-sandbox validation gate --request request.json --scope workflow-lot-id
snipara-sandbox validation run --request request.json --store /absolute/proof-store
snipara-sandbox validation status --run-id <id> --store /absolute/proof-store
snipara-sandbox validation verify --request request.json --run-id <id> --store /absolute/proof-store
snipara-sandbox validation cancel --run-id <id> --store /absolute/proof-store
```

`gate` runs an isolated readiness probe. If the required environment cannot run,
it returns `needs_user_choice` with a question. The choices are another named
environment, installing/configuring Sandbox, or continuing without Sandbox.
For the latter distinguish local tests from no tests. No answer or a CLI flag
chosen by an agent alone is human consent. To record an actual operator choice:

```sh
snipara-sandbox validation choose --request request.json --scope workflow-lot-id \
  --choice local --acknowledge-user-choice
```

Alternative choices require `--environment <name>`. Recorded choices are
operator assertions, expire after 24h, and are bound to the workspace, scope,
profile, image, cwd and resource/access limits. They survive resume within that
scope. They never execute an alternative runner themselves, waive a mandatory
proof or cause `validation run` to leave Docker. A job can fail even if its
readiness probe passed. An explicit local alternative does not become Docker
isolation evidence.

## Boundaries and evidence

Inputs are copied from a filtered snapshot including authorized uncommitted
files, not just a commit. `.git`, `.snipara`, environments, common credential
files, keys and generated dependencies are excluded. All symlinks and special
files in included sources are refused. Do not keep credentials in ordinary source
files; the filtering policy cannot identify all private content automatically.
Sources are immutable inputs; commands write only to the disposable `/work` and
`/tmp`. Containers are non-root, network-off, capability-free, with memory,
CPU, PID, tmpfs disk and output limits. Docker's daemon is trusted local
infrastructure; this is not a qualification for hostile shared multi-tenancy.

The durable store must be outside the input tree or beneath its excluded
`.snipara` directory. Protect it as local operator state. Receipts identify exact
source bytes/modes and exclusions, request and command hashes, resolved image,
runtime versions, exit status, output and artifacts. Verify rechecks these inputs
and artifact/log bytes. Receipts are local runtime observations, not signed
remote attestations. A historical receipt does not restore a running process.
File artifacts must be regular files; links and oversized exports are refused.

Timeout and explicit cancellation remove only containers owned by that run.
Keep evidence before cleaning stores. If the host process is killed abruptly,
use the recorded container ID and ownership label when recovering its resources;
never run global container cleanup. Crash recovery is not yet automatic.

MCP exposes `validation_diagnose`, `validation_run`, `validation_status`,
`validation_verify`, and `validation_cancel`. Repository access is confined to
`docker_workspace_path` from operator config, or the server's working directory.
No client-supplied arbitrary host root is accepted. Reload a native client after
upgrading to load the new schemas. Existing `execute_python` sessions and their
REPL backends remain separate; use repository validation for the lifecycle and
proof contract described here. The old REPL Docker path is not covered by these
new job guarantees.

## Harness integration

Companion and Orchestrator consume the public CLI as adapters. Probe capabilities
before executable validation. Missing executable, old schema, incompatible image,
missing daemon or user refusal must never trigger local fallback. Install a
Sandbox verifier as a policy-owned required proof for new Controller lots.
The verifier can reject changed sources, commands, images or artifacts before
integration; neither a choice receipt nor a readiness probe is a passing proof.
Existing Controller contracts keep their stored floors and require explicit
policy migration if adding this requirement.

Qualification targets macOS/Linux Docker. Windows and remote/multi-tenant engines
are not qualified by this release. No package is considered published merely
because the candidate sources or local wheels exist.
