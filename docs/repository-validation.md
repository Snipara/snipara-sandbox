# Repository validation (2.4.0)

Sandbox remains standalone. No Snipara account, provider key, LLM inference,
Companion or Orchestrator is needed for these deterministic commands.
Install the validation extra (`snipara-sandbox[validation]`) and select a prepared
image. The legacy `[docker]` extra remains supported. Add `[mcp]` for the MCP
transport; no inference, visualization or development extra is required.
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

For a broader prepared image and project-specific capability probes, see
[the development toolbox](validation-toolbox.md). Optional `requirements`
check Python imports, Node package resolution and executable availability before
dispatch; missing dependencies fail readiness without installation or host fallback.

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
files, keys and generated dependencies are excluded. All symlinks, hardlinks and special
files in included sources are refused. Do not keep credentials in ordinary source
files; the filtering policy cannot identify all private content automatically.
Snapshots preserve empty directories and normalize copied permissions for the
container user, independently of host umask. Streaming descriptor traversal runs
in a disposable helper so source I/O can be cancelled or timed out.
Sources are immutable inputs; commands write only to the disposable `/work` and
`/tmp` and `/dev/shm`. Their combined writable capacity is bounded by the
request disk budget. Required Linux daemon CPU, memory, PID and swap support
is checked before dispatch. Containers are non-root, network-off, capability-free, with memory,
CPU, PID, tmpfs disk and output limits. Docker's daemon is trusted local
infrastructure; this is not a qualification for hostile shared multi-tenancy.

The durable store must be outside the input tree or beneath its excluded
`.snipara` directory. Protect it as local operator state. State access stays bound to directory
descriptors, including when an ancestor is replaced during a run. Receipts identify exact
source bytes/modes and exclusions, request and command hashes, resolved image,
runtime versions, exit status, output and artifacts. Verify rechecks these inputs
and artifact/log bytes. Receipts are local runtime observations, not signed
remote attestations. A historical receipt does not restore a running process.
File artifacts must be regular files; links and oversized exports are refused.

Timeout and explicit cancellation target only containers owned by that run.
The command timeout has a fixed control/cleanup grace of at most 30 seconds.
Daemon transactions run in killable owner processes. If daemon control or cleanup
cannot finish within that bound, the run fails with `cleanup_complete=false`;
explicit recovery is required and verification refuses acceptance. Recovery
refuses an active lifetime owner lock.
Keep evidence before cleaning stores. If the host process is killed abruptly,
run `validation recover --run-id <id> --store <store>` to remove only its
label-checked container and record interrupted status. Recovery is explicit, not
automatic; never run global container cleanup.

MCP exposes `validation_diagnose`, `validation_run`, `validation_status`,
`validation_verify`, `validation_cancel`, and `validation_recover`. Repository access is confined to
`SNIPARA_SANDBOX_VALIDATION_WORKSPACE` from the operator launch environment, or
the server's working directory. Project dotenv files cannot select the daemon
or enlarge this validation root. The root is pinned through directory descriptors.
No client-supplied arbitrary host root is accepted. Reload a native client after
upgrading to load the new schemas. Existing `execute_python` sessions and their
REPL backends remain separate; use repository validation for the lifecycle and
proof contract described here. The old REPL Docker path is not covered by these
new job guarantees.

## Container engine

Validation talks to the Docker Engine API; it does not need Docker Desktop. The engine is the one of the selected
`docker context`, or of `DOCKER_HOST` when it is set (it wins over the context). Only a local Unix socket is
accepted. Images are looked up in that engine's own store, so build or load the prepared image there.

```bash
docker context ls
DOCKER_CONTEXT=<name> snipara-sandbox validation gate --request request.json --scope my-check
```

What a job needs from an engine:

- `info` reports a Linux engine with memory, CPU quota, PID and swap limits (checked before dispatch);
- a container can be created with an init process, a read-only root, user `65534:65534`, no network, every
  capability dropped, `no-new-privileges`, memory and swap limits, a CPU quota, a PID limit, a `/dev/shm` size, sized
  `tmpfs` mounts for `/work` and `/tmp`, a read-only bind mount and the `json-file` log driver;
- `exec` create, start and inspect, and `kill`.

Loopback inside the job works by address (`127.0.0.1`, `::1`); with the network off there is no resolver file, so
the name `localhost` may not resolve. An engine that accepts these options without enforcing them gives no
isolation, so check a new engine before trusting its results:

```bash
DOCKER_CONTEXT=<name> snipara-sandbox validation engine --image <a local Linux image>
```

It creates one container with the settings above, enters it with `exec` and tests each property from inside (user id,
read-only root, writable and executable `/work`, `/tmp` size, read-only input, no route and no active interface,
loopback, init process, no capability, `no_new_privs`, memory and process limits), then kills and removes it. It
prints a JSON report and exits 2 when a property is not honoured. The loopback test needs `python3` in the image and
is reported as skipped without it.

## Writable space

`/work`, `/tmp` and `/dev/shm` are `tmpfs` mounts cut from the request's `disk_mb`. `/tmp` and `/dev/shm` each get a
quarter of the budget, capped at 64 MiB; `/work` gets the rest. A test suite that writes more than 64 MiB of
temporary files fails with "No space left on device" whatever `disk_mb` says: point its temporary directory at a
directory under `/work` for that command (`TMPDIR=/work/.tmp`), keeping `/tmp` for tools that expect it.

## Profile names

A request's `profile` is `python`, `node` or `mixed`. `validation prepare --profile` takes a catalogue name
(`python-ci`, `node-ci`; list them with `validation profiles`) and writes the matching request profile: a request
prepared with `--profile python-ci` records `"profile": "python"`.

## Reading an unknown source tree

The same commands give a place to inspect code you do not trust yet, before building or installing it: the tree is
copied read-only into a container with no network, no capability and an unprivileged user, and nothing from it has
to be executed.

1. Clone or unpack the tree into an empty directory. Keep your own inspection script in a sibling directory of
   the same workspace, not inside the tree.
2. Prepare, gate and run with any image that has the interpreter of your script:

   ```bash
   snipara-sandbox validation prepare --profile python-ci --workspace /abs/audit --image <image-id> \
     --output request.json -- bash -c 'cd /work && python3 -I tools/scan.py /work/src'
   snipara-sandbox validation gate --request request.json --scope audit
   snipara-sandbox validation run --request request.json --store /abs/audit-store
   ```

   `python3 -I` keeps the interpreter from importing modules that sit next to the script or in the tree.
3. The command's output is `output.log` in the run's directory of the store; the receipt printed by `run` gives the
   status and timings.

This is a static reading aid (inventory of binaries, dependency sources, endpoints, privileged calls). It says
nothing about a released binary that was not built from that tree.

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

Scope binding uses `snipara.validation.scope.v2`: SHA256 of a compact UTF-8 JSON
array of strings: contract version, workflow scope, request schema, normalized absolute workspace (only root-owned OS aliases resolve),
profile, image, normalized cwd, timeout seconds, enforced integer nano-CPU budget,
memory MiB, PID budget and aggregate disk MiB. Command argv and requested artifacts
are deliberately excluded so a choice remains usable within the same environment
and lot; execution receipts still bind the exact command and artifacts.
Consumers independently compute the expected scope from the supplied request;
returned hashes are never their own authority. Cross-language vectors are in
`contracts/validation-scope-v2.json`. Older candidate scope hashes are invalidated.
