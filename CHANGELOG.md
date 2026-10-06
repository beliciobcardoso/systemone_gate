# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-10-06

### Added

- **Behavior change:** the deterministic guard rules (`systemone-gate hook-guard`, `guard_command`) now also block infrastructure, cloud, database and git commands that destroy shared state without an interactive confirmation: `terraform destroy -auto-approve`, `kubectl delete namespace <production-looking name>` and `kubectl delete pvc --all`, `aws s3 rb --force` and recursive `aws s3 rm` on a bucket root, `gcloud`/`az` deletes with `--quiet`/`--yes`, `docker system prune --volumes` and `docker volume prune -f`, `dropdb`, `pg_dropcluster`, `mysqladmin -f drop`, `redis-cli FLUSHALL/FLUSHDB`, `userdel -r`, `crontab -r`, `git branch -D main/master`, `git clean -fx`, `find / ... -delete`, `shred` of keys or devices, `mv x /dev/null`, `rm -rf .git` (the repository's own), and `rm -rf` of `/etc/<x>`, `/boot/<x>`, `/usr/{bin,lib}`, `/var/lib` and its children or a database data directory. Scoped or prompting variants keep working (`kubectl delete pod`, `terraform plan`, `aws s3 rm s3://b/tmp/ --recursive`, `rm -rf /var/lib/apt/lists/*`). Commands that were previously only a model warning can now be blocked, so a pipeline that relied on running one of them must change.
- `docs/GUARD_CALIBRATION.md` (and `.pt-BR.md`): the calibration result. No threshold of `tev1:0.8b` or `nimble` met recall >= 90% with FPR <= 5% out of sample, so the thresholds stay unchanged and the rules were widened instead. The post-widening recall on the dataset is in-sample; a held-out set is needed to calibrate a model again.
- Second labels for the 84-case synthetic guard dataset (`benchmarks/data/calibration/guard_synthetic.json`), produced blind by `gemini-2-5` through `benchmarks/calibrate_label.py`: 84 agreed, 0 disputed, so every case is now final. The set is synthetic and mostly unambiguous, so full agreement does not cover real-world borderline commands.

## [0.5.0] - 2026-10-06

### Changed

- **Behavior change:** every message the library, CLI, git hook and MCP server print or return is now in English: CLI output and `--help`, `doctor` checks and hints, the pre-commit hook script, the deterministic guard-rule reasons returned to agents, policy and client error messages, the MCP tool errors and the `systemone_review_staged` note (`no staged changes`), the diff coverage lines and skip reasons (`binary`, `minified/generated`, `file limit`), and the ASCII fallback tokens (`[ERRO]`, `[AVISO]`, `[INSPECAO]`, `[TRIAGEM]`, `[DICA]`, `[ARQUIVOS]` and `[RISCO]` are now `[ERROR]`, `[WARN]`, `[INSPECT]`, `[TRIAGE]`, `[HINT]`, `[FILES]` and `[RISK]`). Anything that matched the Portuguese text (scripts parsing stderr, assertions on `reasons`, the `invalid response: ` prefix) must be updated. JSON field names, error kinds, exit codes and choice keys are unchanged. A pre-commit hook installed by an earlier version keeps its Portuguese messages until it is reinstalled with `systemone-gate install-hook`.
- Documentation is now bilingual: English is the primary language and Brazilian Portuguese the secondary one. `README.md`, `docs/AGENT_MANUAL.md` and `examples/cursor_rules.md` are in English, with `*.pt-BR.md` translations and a language switcher at the top of each. `docs/MANUAL_AGENTES_IA.md` was renamed to `docs/AGENT_MANUAL.pt-BR.md`; `examples/python_agent_integration.py` is now English only. `docs/BENCHMARK_RUBRIC_LANGUAGE.md` stays in Portuguese (frozen benchmark record) with an English summary on top.

## [0.4.0] - 2026-10-06

### Changed

- **Behavior change:** every rubric sent to the model is now in English (`default`, `generic` and `web-backend` profiles, `RUBRIC_COMMAND_SAFETY`, `RUBRIC_AGENT_ROUTING`, the `doctor` smoke rubric), and so is the truncation marker appended to long diffs (`... [truncated N lines]`). The project is public and international. The benchmark in `docs/BENCHMARK_RUBRIC_LANGUAGE.md` found no detectable accuracy difference between Portuguese and English for the default profile (smallest p = 0.19); the other rubrics were not part of that benchmark. A later comparison on the guard rubric found no change for `nimble:latest` but a worse score separation for `tev1:0.8b`, the guard's production model (paired AUC difference -0.104, 95% CI [-0.182, -0.030], 69 cases, labels not yet reviewed); in practice the model blocks nothing on its own at the default threshold in either language, so current behavior is unchanged, but it lowers what that model can reach if it is ever calibrated as a gate (see `docs/BENCHMARK_RUBRIC_LANGUAGE.md`). Choice keys (the labels the policy reads) are unchanged. Model scores depend on the prompt, so any threshold or calibration measured with the Portuguese rubrics no longer applies; the rubric hash recorded by `calibrate_collect.py` flags stale collections. A test now rejects Portuguese text in any rubric. The Portuguese original of the default profile is frozen in `benchmarks/data/rubrics_pt.py` so the benchmark stays reproducible.

### Added

- `benchmarks/calibrate_label.py` runs the second-labeling workflow for the calibration datasets: a blind export (commands and definition only), import of the second labeler's verdicts (`agreed` / `disputed`), a disputes sheet for the human reviewer, and `resolve`. The dataset schema now requires `second_labeler` whenever `second_label` is set. Step 4 of the threshold calibration.
- `benchmarks/calibrate_analyze.py` evaluates the production guard decision (`policy.decide_command`) over a grid of `guard_danger_threshold`, `min_confidence` and `guard_on_error` against the collected outputs and reviewed labels, and reports recall/FPR with Wilson intervals from a stratified k-fold cross-validation. It refuses incomplete or stale data, and a recommendation is only emitted when the held-out result meets the criterion on fully reviewed labels (`--preliminary` previews without one). Step 3 of the threshold calibration.
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
