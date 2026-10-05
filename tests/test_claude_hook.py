import json
import subprocess
import sys
from pathlib import Path

import pytest

from systemone_gate.claude_hook import run_pretooluse

ROOT = Path(__file__).resolve().parent.parent
WARNING = "[SystemOne Gate] aviso: payload inválido, comando não verificado."


def payload(tool="Bash", **tool_input):
    return json.dumps({"tool_name": tool, "tool_input": tool_input})


def test_blocks_catastrophic_bash_command():
    code, msg = run_pretooluse(payload(command="rm -rf /"))
    assert code == 2
    assert msg.startswith("[SystemOne Gate] Comando bloqueado: ")
    assert "(regra rm-recursive-root)" in msg


@pytest.mark.parametrize(
    "stdin_text",
    [
        payload(command="ls -la"),
        payload(tool="Write", command="rm -rf /"),
        json.dumps({"tool_name": "Bash"}),
        json.dumps({"tool_name": "Bash", "tool_input": {}}),
        json.dumps({"tool_name": "Bash", "tool_input": {"command": 42}}),
        json.dumps({"tool_name": "Bash", "tool_input": "rm -rf /"}),
        json.dumps({}),
    ],
)
def test_allows_without_message(stdin_text):
    assert run_pretooluse(stdin_text) == (0, "")


@pytest.mark.parametrize("stdin_text", ["", "   ", "not json", "{", "[1, 2]", "null"])
def test_invalid_payload_fails_open_with_warning(stdin_text):
    assert run_pretooluse(stdin_text) == (0, WARNING)


def _run_cli(stdin_text):
    return subprocess.run(
        [sys.executable, "-m", "systemone_gate.cli", "hook-guard"],
        input=stdin_text, capture_output=True, text=True, cwd=str(ROOT), timeout=30,
    )


def test_cli_hook_guard_blocks_with_exit_2():
    proc = _run_cli(payload(command="ls && rm -rf /"))
    assert proc.returncode == 2
    assert "Comando bloqueado" in proc.stderr


def test_cli_hook_guard_allows_with_exit_0():
    proc = _run_cli(payload(command="ls -la"))
    assert proc.returncode == 0
    assert proc.stderr == ""


def test_cli_hook_guard_invalid_json_warns_and_exits_0():
    proc = _run_cli("garbage")
    assert proc.returncode == 0
    assert "payload inválido" in proc.stderr
