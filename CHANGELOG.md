# Changelog

All notable changes to this project are documented in this file.

## Unreleased

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
