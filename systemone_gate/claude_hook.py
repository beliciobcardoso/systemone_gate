"""
Claude Code PreToolUse hook: deterministic, offline enforcement outside the
agent. Never calls the model. Exit code 2 makes Claude Code block the tool
call and show stderr to the model.
"""

import json
from typing import Tuple

from .guard_rules import evaluate_command

EXIT_ALLOW = 0
EXIT_BLOCK = 2
INVALID_PAYLOAD_WARNING = "[SystemOne Gate] aviso: payload inválido, comando não verificado."


def run_pretooluse(stdin_text: str) -> Tuple[int, str]:
    """Evaluate a PreToolUse payload; returns (exit code, stderr message)."""
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
    match = evaluate_command(command)
    if match is None:
        return EXIT_ALLOW, ""
    return EXIT_BLOCK, f"[SystemOne Gate] Comando bloqueado: {match.reason} (regra {match.rule_id})"
