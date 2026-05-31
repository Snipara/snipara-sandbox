# Changelog

All notable changes to this project are documented in this file.

## Unreleased

## [2.2.7] - 2026-05-31

### Added

- Added `file_search`, a bounded source-search tool backed by `rg` with a Python
  fallback, so autonomous agents can find exact files and lines before reading.

### Fixed

- Capped `file_read` and `list_files` output sizes, ignored noisy generated and
  vendor directories, and refused broad recursive listings to prevent autonomous
  Sandbox runs from spending their budget on repo-wide inventory.
- Updated the autonomous agent prompt to prefer targeted search and focused line
  reads over broad file listing.

## [2.2.6] - 2026-05-31

### Fixed

- Prevented `snipara_agent_run` from corrupting the MCP stdio transport by
  redirecting agent startup and runtime stdout noise to stderr. This keeps JSON-RPC
  frames clean when structlog or other code writes to stdout during agent launch.

## [2.2.5] - 2026-05-29

### Added

- `docker_workspace_install_extras`: a scoped list of pip requirements installed
  on top of any setup mode, so a workspace can pull just the runtime deps its
  test collection needs (e.g. a `conftest.py` that imports `fastapi` or
  `pydantic-settings`) without a full `.[dev]` install. Settable via config,
  direct instantiation, or the `SNIPARA_SANDBOX_DOCKER_WORKSPACE_INSTALL_EXTRAS`
  env var (comma/space-separated). Extras are validated as pip requirement
  specifiers to keep them out of the image's `RUN` line.

### Fixed

- Workspace-image builds no longer set the removed `--cache-dir` pytest CLI flag.
  `PYTEST_ADDOPTS` now uses the portable `-o cache_dir=/tmp/pytest-cache` ini
  override, which works on both pytest 8 and pytest 9 (pytest 9 dropped
  `--cache-dir`).

### Validated

- End-to-end: a `tests-only` image plus `docker_workspace_install_extras` runs a
  real repo test suite to green inside the isolated container (sandbox-owned
  context ships `tests/` + `README.md`, `PYTHONPATH=/workspace` resolves imports,
  scoped extras satisfy the conftest).

## [2.2.4] - 2026-05-29

### Fixed

- Workspace-image builds no longer crash with `AttributeError: 'dict' object has
  no attribute 'decode'`. `decode=True` was passed to the high-level
  `images.build()`, which already decodes its log stream internally; removing it
  lets the build run. Validated end-to-end: a `tests-only` image builds in ~7s
  and the sandbox-owned context correctly ships `tests/` and `README.md`
  (editable metadata generation now succeeds).

## [2.2.3] - 2026-05-29

### Changed

- Added a `tests-only` Docker workspace setup mode that installs `pytest` and
  `pytest-asyncio`, then exposes the repo with `PYTHONPATH=/workspace` so
  isolated test runs do not require a full editable package install.

### Fixed

- Workspace-image builds now use a sandbox-owned build context instead of the
  project's `.dockerignore`, so repo-backed test runs keep `tests/`,
  `README.md`, and packaging metadata available inside the image.
- Docker workspace-image failures now surface recent build log lines instead of
  only returning Docker's opaque high-level `BuildError`.

### Remaining limitations

- `dev` still installs the full project dependency set even when the target
  tests only need a small subset; targeted extras or scoped installs are not
  implemented yet.
- Projects whose tests depend on generated artifacts or non-Python setup steps
  still need explicit custom install commands or follow-up hooks; there is no
  first-class `post_install` hook yet.

## [2.2.2] - 2026-05-29

### Changed

- Added `docker_workspace_setup` and `docker_workspace_install_command` so Docker mode can prepare a cached workspace image with project dependencies for isolated repo-backed test runs.
- Updated the Docker runtime, CLI, docs, and tests to support prepared workspace images while keeping runtime execution on a read-only mount with network disabled.

## [2.2.1] - 2026-05-29

### Fixed

- `snipara-sandbox run` now honors `trust_level = "local"` when `environment = "local"`, so documented repo-local development flows use `LocalDevREPL` instead of the restricted sandbox.
- `snipara-sandbox config show` now explains that empty `allowed_paths` means the current working directory only, rather than implying no file access.

### Changed

- Docker executions now mount the current workspace read-only at `/workspace` by default, with `docker_mount_workspace` and `docker_workspace_path` available to disable or override that behavior.
- Added `docker_workspace_setup` and `docker_workspace_install_command` so Docker mode can prepare a cached workspace image with project dependencies for isolated repo-backed test runs.
- Updated Docker runtime, CLI, docs, and tests to reflect the repo-visible isolated execution model.

## [2.2.0] - 2026-05-11

### Changed

- Renamed the published Python distribution to `snipara-sandbox`.
- Added the `snipara-sandbox` CLI while keeping `rlm` as a legacy command alias.
- Added the `snipara_sandbox` Python import surface while keeping `rlm` imports compatible.
- Renamed public MCP server identity and agent tools to Snipara-first names, with legacy `rlm_*` aliases retained.
- Switched generated API key examples to the `snp-` prefix.
- Updated documentation, install scripts, and package metadata for the `Snipara/snipara-sandbox` repository.

## [2.1.3] - 2026-04-29

### Added

- `snipara-sandbox config show` for inspecting the effective runtime configuration.
- `--json` output for `snipara-sandbox config show`.

### Changed

- Added documentation links for the config inspection command in the README and configuration guide.
- Bumped the package version to `2.1.3` for release.

## [2.1.2] - 2026-04-29

### Fixed

- `rlm --version` now works from the root CLI entrypoint.
- CLI version output now reflects the source version and the installed package version when they differ.
- Project `.env` files are loaded automatically by the CLI and config loader, so `snipara-sandbox doctor` and `snipara-sandbox run` no longer depend on manual `source .env`.
- Failed runs now return non-zero exit codes instead of reporting `success: true`.
- CLI JSON mode now emits clean JSON without debug logs mixed into stdout.
- `--max-depth 0` is rejected immediately with a clear error.

### Changed

- Added explicit failure propagation to `RLMResult`.
- Added tests covering CLI version handling, `.env` loading, JSON output, and max-depth validation.
- Documented the confirmed bugs, fixes, and remaining notes in `docs/snipara-sandbox-audit-2026-04-29.md`.

### Notes

- `--max-depth 1` is still a tight setting and may fail on prompts that need at least one extra recursive or tool step.
- The repository's GitHub release workflow attempted trusted publishing, but PyPI rejected the repo as an invalid trusted publisher. The `2.1.2` package was published successfully with the existing local PyPI token fallback.
