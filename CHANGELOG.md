# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Pytest suite with a fake Ollama server covering the client, the MCP server and the CLI (#5).
- `SYSTEMONE_TIMEOUT` environment variable and `timeout` client argument (default 30 s); an invalid value exits the CLI with code 2 (#12).
- Per-file staged diff review: lockfiles, binaries and minified/generated files are skipped, each file is truncated independently, and a coverage summary reports what was and was not inspected (#6).
- Policy layer (`systemone_gate.policy`) that centralizes block/allow decisions for diffs and commands, with configurable thresholds and error behavior via environment variables (#7).
- Deterministic command guard rules (offline, no model) and the `hook-guard` subcommand, a Claude Code `PreToolUse` hook that blocks catastrophic commands (#8).
- `uninstall-hook` subcommand, `--plain` flag and `SYSTEMONE_PLAIN` environment variable for ASCII-only output, and `CHANGELOG.md` (#14).
- `AGENTS.md` with git workflow rules (#1) and a problem analysis report (`docs/ANALISE_PROBLEMAS.md`) with classified findings and a fix plan (#2).

### Changed

- Block/allow decisions moved out of the CLI into the policy layer (#7).
- Staged diff is reviewed per file instead of truncating the whole diff as a single blob (#6).
- The git hook generator now pins the absolute Python interpreter path, preserves an existing foreign hook as a backup and runs it first, and no longer blocks commits when the package is unavailable (fail-open) (#4).
- Project metadata: author entry without placeholder email, `[project.urls]` added, `.mypy_cache/` and `.ruff_cache/` ignored (#14).
- Review caps (250 lines per file, 20 files) and the `git diff` timeout are named constants (#14).
- Documentation: the green-CI requirement was dropped from the git workflow (#10) and resolved items were marked in the problem analysis report (#9).

### Removed

- The no-op `--tev` option of `systemone-gate diff` (Tev1 0.8B is already the default model) (#14).

### Fixed

- MCP server no longer dies on `null` or invalid `params`/`arguments`; invalid tool arguments get a JSON-RPC `-32602` error and unexpected tool failures return `isError` without leaking details on stdout (#3).
- Installing the hook no longer overwrites an existing backup, and uninstalling refuses to remove a hook that is not from SystemOne Gate (#4).
- Hooks directory is resolved through `git rev-parse --git-path hooks`, so linked worktrees, submodules and `core.hooksPath` work (#11).
- Client reports HTTP errors (with a bounded server error body) and timeouts accurately instead of a generic failure (#12).
- MCP server answers unparsable JSON lines with a JSON-RPC `-32700` parse error, and tool results carrying an `error` key are flagged `isError` (#13).
- `systemone-gate diff` has a 30 s timeout on `git diff --cached` (#14).
- Emoji output no longer crashes with `UnicodeEncodeError` on non-UTF-8 terminals or pipes (e.g. `PYTHONIOENCODING=ascii`) (#14).

## [0.1.0] - 2026-10-05

### Added

- Initial release: `systemone-gate` CLI (`diff`, `triage`, `guard`, `install-hook`), stdio MCP server (`systemone-mcp`), pre-commit git hook, Ollama System One client, rubric definitions, Python integration example and the AI agents manual.
