"""
Command Line Interface (CLI) for SystemOne Gate.
Provides dev tools and can start the MCP server directly.
"""

import argparse
import json
import os
import subprocess
import sys
from contextlib import nullcontext
from typing import List, Optional

from . import __version__
from .claude_hook import run_pretooluse
from .client import SystemOneClient
from .diff_review import DEFAULT_MAX_LINES_PER_FILE, format_coverage, review_staged
from .doctor import run_cli as run_doctor_cli
from .hooks import install_git_hook, uninstall_git_hook
from .mcp_server import run_mcp_server
from .output import plain_output
from .policy import (
    ACTION_BLOCK,
    DiffReview,
    InvalidResponse,
    PolicyConfig,
    diff_near_miss,
    evaluate_command,
    evaluate_diff,
    is_low_confidence,
    parse_command_check,
    parse_diff_review,
)
from .rubrics import DEFAULT_PROFILE, PROFILES

EXIT_CONFIG_ERROR = 2
DEFAULT_DIFF_MODEL = "nimble"  # tev1:0.8b did not discriminate diff risk in docs/BENCHMARK_RUBRIC_LANGUAGE.md
DIFF_MODEL_ENV = "SYSTEMONE_DIFF_MODEL"
NIMBLE_MODEL = "nimble"
GIT_DIFF_TIMEOUT_SECONDS = 30
PROFILE_HELP = f"Rubric profile (default: env SYSTEMONE_PROFILE or {DEFAULT_PROFILE})"

def _try_parse(parser, res):
    """Parses a response for display only; the verdict comes from the policy layer."""
    try:
        return parser(res)
    except InvalidResponse:
        return None

def _confidence_suffix(confidence: Optional[float]) -> str:
    return "" if confidence is None else f" (confidence {confidence:.2f})"

def _invalid_config(error: ValueError) -> int:
    print(f"❌ Invalid configuration: {error}", file=sys.stderr)
    return EXIT_CONFIG_ERROR

def _print_diff_report(review: DiffReview, res: dict, cfg: PolicyConfig) -> None:
    risk_info = res["answers"]["risk_level"]
    print("\n------------------ Impact Report ------------------")
    print(f"📊 Technical Risk Level: {review.risk_score:.2f} / 2.0{_confidence_suffix(review.confidence)}")
    legend = risk_info.get("legend")
    level_probs = risk_info.get("probabilities")
    if isinstance(legend, dict) and isinstance(level_probs, dict):
        for idx, desc in legend.items():
            prob = level_probs.get(idx, 0.0)
            print(f"   • Level {idx} ({prob*100:.1f}%): {desc}")

    print(f"\n⚠️  Breaking Change: {review.breaking_choice.upper()}")
    for opt, prob in review.breaking_probs.items():
        print(f"   • {opt}: {prob*100:.1f}%")
    print(
        f"\n   Block thresholds: risk > {cfg.diff_risk_threshold} and "
        f"breaking_change > {cfg.diff_breaking_threshold} (both required)"
    )
    print("----------------------------------------------------------\n")

def _inside_git_repo() -> bool:
    try:
        proc = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True,
                              timeout=GIT_DIFF_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return True  # let the diff call below report the real failure
    return proc.returncode == 0

def handle_diff(client: SystemOneClient, model: str, max_lines: int = DEFAULT_MAX_LINES_PER_FILE,
                profile: Optional[str] = None) -> int:
    if not _inside_git_repo():
        print("❌ Not inside a Git repository: run this from a repository with staged changes.", file=sys.stderr)
        return 1
    try:
        diff_output = subprocess.check_output(["git", "diff", "--cached"], text=True,
                                              timeout=GIT_DIFF_TIMEOUT_SECONDS)
    except Exception as e:
        print(f"❌ Failed to run 'git diff --cached': {e}", file=sys.stderr)
        return 1

    if not diff_output.strip():
        print("ℹ️  No staged changes found (git diff --cached is empty). Commit allowed.")
        return 0

    line_count = len(diff_output.splitlines())
    print(f"🔍 [SystemOne Gate] Inspecting diff ({line_count} lines) with model '{model}'...")
    try:
        res = review_staged(client, diff_output, model, max_lines_per_file=max_lines, profile=profile)
    except ValueError as e:
        return _invalid_config(e)
    for coverage_line in format_coverage(res.get("coverage", {})):
        print(coverage_line)

    try:
        cfg = PolicyConfig.from_env()
    except ValueError as e:
        print(f"❌ Invalid policy configuration: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    decision = evaluate_diff(res, cfg)
    if decision.warning:
        print(f"⚠️  [SystemOne Gate] Warning: {decision.warning}", file=sys.stderr)

    review = _try_parse(parse_diff_review, res)
    if review is not None:
        _print_diff_report(review, res, cfg)

    if decision.action == ACTION_BLOCK:
        if review is not None and not is_low_confidence(review.confidence, cfg):
            print("❌ [BLOCKED] Critical risk and contract break detected!", file=sys.stderr)
        else:
            print(f"❌ [BLOCKED] {'; '.join(decision.reasons)}", file=sys.stderr)
        return 1

    near_miss = diff_near_miss(review, cfg) if review is not None else None
    if near_miss:
        print(f"⚠️  [SystemOne Gate] Warning: {near_miss}", file=sys.stderr)

    if decision.warning:
        return 0  # Fail open (diff_on_error=allow): never block offline work when Ollama is down
    print("✅ [APPROVED] Check completed successfully.")
    return 0

def handle_triage(client: SystemOneClient, error_text: str, model: str, profile: Optional[str] = None) -> int:
    print(f"🩺 [SystemOne Gate] Triaging error with model '{model}'...\n")
    try:
        res = client.triage_error(error_text, model=model, profile=profile)
    except ValueError as e:
        return _invalid_config(e)

    if "error" in res:
        print(f"❌ Error: {res['error']}", file=sys.stderr)
        return 1

    answers = res.get("answers", {})
    print(json.dumps(answers, indent=2, ensure_ascii=False))
    return 0

def handle_guard(client: SystemOneClient, command_text: str, model: str) -> int:
    res = client.guard_command(command_text, model=model)
    try:
        cfg = PolicyConfig.from_env()
    except ValueError as e:
        print(f"❌ Invalid policy configuration: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    decision = evaluate_command(res, cfg)
    if decision.warning:
        print(f"⚠️  [SystemOne Gate] Warning: {decision.warning}", file=sys.stderr)

    check = _try_parse(parse_command_check, res)
    if check is not None:
        print(f"🛡️  Command: {command_text}")
        print(f"• Destructive: {check.choice}{_confidence_suffix(check.confidence)}")
        print(f"• Danger score: {check.danger_score:.2f} / 2.0")

    if decision.action == ACTION_BLOCK:
        if check is not None and not is_low_confidence(check.confidence, cfg):
            print("❌ [COMMAND BLOCKED] High destructive risk!", file=sys.stderr)
        else:
            print(f"❌ [COMMAND BLOCKED] {'; '.join(decision.reasons)}", file=sys.stderr)
        return 1
    return 0

def main(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(
        prog="systemone-gate",
        description="Local low-latency gatekeeper and triage engine for AI agents and developers."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--plain", action="store_true",
                        help="ASCII-only output (no emoji); also via SYSTEMONE_PLAIN=1")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # diff
    p_diff = subparsers.add_parser("diff", help="Inspect staged changes (git diff --cached)")
    diff_model = p_diff.add_mutually_exclusive_group()
    diff_model.add_argument("--nimble", action="store_true",
                            help=f"Shortcut for --model {NIMBLE_MODEL} (already the default)")
    diff_model.add_argument(
        "--model", default=None,
        help=f"Ollama model (default: {DEFAULT_DIFF_MODEL}; also via {DIFF_MODEL_ENV})")
    p_diff.add_argument("--profile", choices=PROFILES, default=None, help=PROFILE_HELP)

    # triage
    p_triage = subparsers.add_parser("triage", help="Triage build errors, test failures or logs")
    p_triage.add_argument("error_text", nargs="+", help="Error text or stack trace")
    p_triage.add_argument("--model", default="nimble", help="Ollama model to use (default: nimble)")
    p_triage.add_argument("--profile", choices=PROFILES, default=None, help=PROFILE_HELP)

    # guard
    p_guard = subparsers.add_parser("guard", help="Check whether a shell command risks destroying data")
    p_guard.add_argument("cmd_text", nargs="+", help="Command to inspect")

    # install-hook
    p_hook = subparsers.add_parser("install-hook", help="Install the pre-commit hook in the current Git repository")
    p_hook.add_argument("--repo", default=None, help="Path of the Git repository")
    p_hook.add_argument("--profile", choices=PROFILES, default=None,
                        help="Rubric profile the hook uses by default (an exported SYSTEMONE_PROFILE still wins)")

    # uninstall-hook
    p_unhook = subparsers.add_parser(
        "uninstall-hook",
        help="Remove the SystemOne Gate pre-commit hook (restores the backup, if any)",
    )
    p_unhook.add_argument("--repo", default=None, help="Path of the Git repository")

    # hook-guard
    subparsers.add_parser(
        "hook-guard",
        help="Claude Code PreToolUse hook: blocks catastrophic commands (offline, no model)",
    )

    # doctor
    p_doctor = subparsers.add_parser(
        "doctor", help="Validate the Ollama backend (version, models and endpoint contract)")
    p_doctor.add_argument(
        "--model",
        action="append",
        default=None,
        help="Model to check (repeatable; default: tev1:0.8b and nimble)",
    )
    p_doctor.add_argument("--no-smoke", action="store_true", help="Skip the contract test (POST /v1/systemone)")

    # mcp
    subparsers.add_parser("mcp", help="Start the MCP stdio server (for Claude Desktop, Cursor, Antigravity)")

    args = parser.parse_args(argv)
    # The MCP server speaks JSON on stdout: it must never go through the translator.
    output_ctx = nullcontext() if args.command == "mcp" else plain_output(args.plain)
    with output_ctx:
        _dispatch(parser, args)

def _resolve_diff_model(args: argparse.Namespace) -> str:
    """--model / --nimble > SYSTEMONE_DIFF_MODEL (non-empty) > default."""
    if args.model:
        model: str = args.model  # argparse Namespace attributes are Any
        return model
    if args.nimble:
        return NIMBLE_MODEL
    return os.environ.get(DIFF_MODEL_ENV) or DEFAULT_DIFF_MODEL

def _dispatch(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    # Commands that never talk to Ollama run before the client is built: an invalid SYSTEMONE_TIMEOUT or
    # OLLAMA_SYSTEMONE_URL must not make them fail (for hook-guard, exit 2 would block every Bash call).
    if args.command == "install-hook":
        sys.exit(0 if install_git_hook(args.repo, profile=args.profile) else 1)

    elif args.command == "uninstall-hook":
        sys.exit(0 if uninstall_git_hook(args.repo) else 1)

    elif args.command == "hook-guard":
        code, message = run_pretooluse(sys.stdin.read())
        if message:
            print(message, file=sys.stderr)
        sys.exit(code)

    elif args.command is None:
        parser.print_help()
        sys.exit(0)

    try:
        client = SystemOneClient()
    except ValueError as e:
        print(f"❌ Invalid configuration: {e}", file=sys.stderr)
        sys.exit(2)

    if args.command == "diff":
        if args.model is not None and not args.model.strip():
            parser.error("--model must not be empty")
        sys.exit(handle_diff(client, model=_resolve_diff_model(args), profile=args.profile))

    elif args.command == "triage":
        err_msg = " ".join(args.error_text)
        sys.exit(handle_triage(client, err_msg, model=args.model, profile=args.profile))

    elif args.command == "guard":
        cmd_msg = " ".join(args.cmd_text)
        sys.exit(handle_guard(client, cmd_msg, model="tev1:0.8b"))

    elif args.command == "doctor":
        sys.exit(run_doctor_cli(client.endpoint, args.model, not args.no_smoke, client.timeout))

    elif args.command == "mcp":
        run_mcp_server()

    else:
        parser.print_help()
        sys.exit(0)

if __name__ == "__main__":
    main()
