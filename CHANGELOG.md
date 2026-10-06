# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `benchmarks/calibrate_collect.py` collects raw model outputs (score, probabilities, confidence) for the calibration datasets with the production rubric, pinning the model digest and a per-case hash; `--resume` retries only failed or changed cases. Step 2 of the threshold calibration.
- Calibration dataset schema and loader (`benchmarks/calibration_schema.py`) with a validator that rejects secrets, non-permissive licenses and labels contradicting the deterministic guard rules, plus a seed set of 84 synthetic guard commands (`benchmarks/data/calibration/`). First step toward calibrating the block thresholds (FAL-03, FAL-05).

## [0.3.1] - 2026-10-05

### Changed

- Docs and package docstring no longer claim "zero cost" or "instantaneous" triage: the wording is now "no cloud-token cost" (local hardware and memory still apply) and measured latency lives in the README.
- Removed the leftover "ultra-fast" claims from the README title, the package docstring and the manual's example skill.
- The package description in `pyproject.toml` no longer says "ultra-fast", which the latency measurements do not support (#35).

### Fixed

- `systemone-gate hook-guard`, `install-hook` and `uninstall-hook` no longer fail when `SYSTEMONE_TIMEOUT` or `OLLAMA_SYSTEMONE_URL` is invalid. Before, `hook-guard` exited with 2, which makes the Claude Code `PreToolUse` hook block every Bash call even though the hook never talks to Ollama.
- A non-numeric `risk_level.score` or `breaking_change` probability in a per-file review no longer raises `TypeError` (a traceback in the pre-commit hook); it is reported as an invalid model response and follows `SYSTEMONE_DIFF_ON_ERROR`.
- Two hook tests assumed that `systemone_gate` was not installed in the interpreter running the tests, so they failed after `pip install -e ".[dev]"`, the documented setup. They now simulate the missing package with a stub interpreter (#36).

## [0.3.0] - 2026-10-05

### Changed

- **Behavior change:** `diff` and the pre-commit hook now review with `nimble` by default (it was `tev1:0.8b`), because `tev1:0.8b` did not discriminate diff risk in the rubric benchmark. The hook uses a 120 s timeout by default, since the first call after an idle period loads the model in 12 to 72 s; the CLI and library default stays 30 s. Hooks installed earlier need `systemone-gate install-hook` again to get the longer timeout. Go back with `SYSTEMONE_DIFF_MODEL=tev1:0.8b` (#29).
- Documentation: how to keep the model loaded in Ollama with `OLLAMA_KEEP_ALIVE` (5 minute default, accepted values, systemd and manual setups, memory trade-off), linked from the pre-commit hook sections (#30).

## [0.2.0] - 2026-10-05

### Added

- `systemone-gate --version` prints the installed version (#27).
- Release process and SemVer policy for 0.x documented in `AGENTS.md`, with a separate authorization for tags and GitHub Releases (#27).
- Pytest suite with a fake Ollama server covering the client, the MCP server and the CLI (#5).
- `SYSTEMONE_TIMEOUT` environment variable and `timeout` client argument (default 30 s); an invalid value exits the CLI with code 2 (#12).
- Per-file staged diff review: lockfiles, binaries and minified/generated files are skipped, each file is truncated independently, and a coverage summary reports what was and was not inspected (#6).
- Policy layer (`systemone_gate.policy`) that centralizes block/allow decisions for diffs and commands, with configurable thresholds and error behavior via environment variables (#7).
- Deterministic command guard rules (offline, no model) and the `hook-guard` subcommand, a Claude Code `PreToolUse` hook that blocks catastrophic commands (#8).
- `uninstall-hook` subcommand, `--plain` flag and `SYSTEMONE_PLAIN` environment variable for ASCII-only output, and `CHANGELOG.md` (#14).
- `systemone-gate doctor` command that checks reachability, the minimum Ollama version (0.35.0), model availability with the `decision` capability and the `/v1/systemone` response shape; the contract test against a real Ollama is opt-in (`pytest -m contract`) (#20).
- Secret redaction of the text sent to the model (eleven kinds of secrets; `SYSTEMONE_REDACT=0` disables it). It is best-effort pattern matching, not a guarantee (#19).
- Rubric profiles `default`, `generic` and `web-backend` for diff review and triage, selected with `--profile` or `SYSTEMONE_PROFILE`. The new profiles are not validated against labeled data (#21).
- Opt-in `SYSTEMONE_MIN_CONFIDENCE`: verdicts below the minimum confidence follow the on-error policy. Off by default because there is no calibration data (#22).
- MCP tool `systemone_review_staged`, which reads the staged diff itself instead of receiving it as an argument (#18).
- `diff --model` option and `SYSTEMONE_DIFF_MODEL` to choose the review model of the pre-commit hook (the default stays `tev1:0.8b`), and `SYSTEMONE_SKIP=1` to skip only this hook's check (#17).
- `benchmarks/latency.py` to reproduce the latency measurements (#16).
- Local quality tooling: `ruff` (E, F, W, I, B, FA102) and `mypy` configured in `pyproject.toml`, plus `scripts/check.sh` that runs lint, format check on new files, types and tests. There is intentionally no CI (#24).
- A/B benchmark of Portuguese versus English rubrics (`benchmarks/rubric_language.py`, `docs/BENCHMARK_RUBRIC_LANGUAGE.md`): no detectable difference at about 35 cases per task, so the rubrics stay in Portuguese. It also found that `tev1:0.8b` does not discriminate diff risk (#25).
- `AGENTS.md` with git workflow rules (#1) and a problem analysis report (`docs/ANALISE_PROBLEMAS.md`) with classified findings and a fix plan (#2).

### Changed

- The version has a single source, `systemone_gate.__version__`: `pyproject.toml` reads it dynamically, and the CLI and the MCP server report it (#26, #27).
- Block/allow decisions moved out of the CLI into the policy layer (#7).
- Staged diff is reviewed per file instead of truncating the whole diff as a single blob (#6).
- The git hook generator now pins the absolute Python interpreter path, preserves an existing foreign hook as a backup and runs it first, and no longer blocks commits when the package is unavailable (fail-open) (#4).
- Project metadata: author entry without placeholder email, `[project.urls]` added, `.mypy_cache/` and `.ruff_cache/` ignored (#14).
- Review caps (250 lines per file, 20 files) and the `git diff` timeout are named constants (#14).
- **Breaking:** the endpoint must be `http` or `https`, and non-loopback hosts now require `SYSTEMONE_ALLOW_REMOTE=1`, with a single warning that data leaves the machine (#19).
- The pre-commit hint suggests `SYSTEMONE_SKIP=1` instead of `git commit --no-verify` (#17).
- Latency claims replaced with measured numbers and a "Desempenho medido" section; the "calibrated probabilities" and "deterministic output" claims were removed from the docs (#16).
- Source and tests now pass `ruff` and `mypy` (imports sorted, unused imports and long lines removed, return and `Optional` types annotated) with no behavior change (#24).
- Documentation: the cold-start range of `nimble` is now about 12-72 s, and the README reports how well each model discriminated diff risk (#25).
- Integration manual checked against vendor documentation; claims that could not be confirmed are marked as unverified (#15).
- Serena project settings (`.serena/project.yml`) are versioned, while its cache and local settings stay ignored (865822a).
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
- Aider section of the manual: the broken `lint-cmd` was replaced by the pre-commit hook plus `git-commit-verify` (#16).
- Emoji output no longer crashes with `UnicodeEncodeError` on non-UTF-8 terminals or pipes (e.g. `PYTHONIOENCODING=ascii`) (#14).

## [0.1.0] - 2026-10-05

### Added

- Initial release: `systemone-gate` CLI (`diff`, `triage`, `guard`, `install-hook`), stdio MCP server (`systemone-mcp`), pre-commit git hook, Ollama System One client, rubric definitions, Python integration example and the AI agents manual.
