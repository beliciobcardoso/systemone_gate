"""
Claude Code PreToolUse hook: deterministic, offline enforcement outside the
agent. Never calls the model. Exit code 2 makes Claude Code block the tool
call and show stderr to the model.
"""

import json
import os
from typing import Mapping, Optional, Tuple

from .guard_protected import load_protected_paths
from .guard_rules import evaluate_command

EXIT_ALLOW = 0
EXIT_BLOCK = 2
INVALID_PAYLOAD_WARNING = "[SystemOne Gate] Warning: invalid payload, command not checked."


def _join(*messages: str) -> str:
    return "\n".join(m for m in messages if m)


def _payload_cwd(payload: dict) -> str:
    cwd = payload.get("cwd")
    return cwd if isinstance(cwd, str) and cwd.startswith("/") else os.getcwd()


def run_pretooluse(stdin_text: str, env: Optional[Mapping[str, str]] = None) -> Tuple[int, str]:
    """Evaluate a PreToolUse payload; returns (exit code, stderr message).

    `env` defaults to the process environment (source of SYSTEMONE_PROTECTED_PATHS).
    """
    try:
        payload = json.loads(stdin_text)
    except (ValueError, TypeError):
        return EXIT_ALLOW, INVALID_PAYLOAD_WARNING
    if not isinstance(payload, dict):
        return EXIT_ALLOW, INVALID_PAYLOAD_WARNING
    if payload.get("tool_name") != "Bash":
        return EXIT_ALLOW, ""
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return EXIT_ALLOW, ""
    protected, warnings = load_protected_paths(env, _payload_cwd(payload))
    notes = _join(*warnings)
    match = evaluate_command(command, protected=protected)
    if match is None:
        return EXIT_ALLOW, notes
    return EXIT_BLOCK, _join(f"[SystemOne Gate] Command blocked: {match.reason} (rule {match.rule_id})", notes)
