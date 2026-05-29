# Changelog

All notable changes to this project are documented in this file.

## Unreleased

### Known issues (from dogfooding the isolated Docker workspace path)

Running real `pytest` inside a Docker image built from a production-ready repo
(`docker_workspace_setup="dev"`, `pip install -e .[dev]`) surfaced friction that
makes the isolated repo-backed path unusable on such repos today:

- **The project's `.dockerignore` is silently reused for the workspace image
  build.** A production-tuned `.dockerignore` typically excludes `*.md` and
  `tests/`. The first breaks editable installs (e.g. `hatchling` raises
  `OSError: Readme file does not exist: README.md` during metadata generation,
  blocking the build immediately); the second means the very tests we want to
  run are never copied into the image. Production images and test sandboxes have
  opposite context needs.

### Planned improvements

- **Use a sandbox-owned `.dockerignore`** for the workspace build context instead
  of the project's: exclude only heavy noise (`.venv`, `node_modules`, `.git`,
  `__pycache__`) while keeping `tests/`, `*.md`, and packaging metadata.
- **Decouple "build the package" from "run the tests".** Add a
  `setup="tests-only"` mode that installs just test deps (`pytest`,
  `pytest-asyncio`) and exposes the repo via `PYTHONPATH=/workspace`, avoiding a
  full editable install that requires intact packaging metadata.
- **Surface build logs on failure.** The high-level Docker SDK raises an opaque
  `BuildError` ("non-zero code: 1"); the real pip error is only visible via the
  low-level `docker.APIClient`. Capture and print the last ~30 build log lines
  when a workspace image build fails.
- **Scope dependencies.** `dev` pulls the entire core dependency set (multi-GB,
  multi-minute) even when target tests only import the stdlib. Allow a targeted
  extras list (e.g. `install_extras=["test"]`).
- **Anticipate codegen/Node steps.** Repos whose tests import an app needing a
  generated client (e.g. Prisma) require a `post_install` hook such as
  `prisma generate`, otherwise non-pure tests fail even after a successful build.

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
