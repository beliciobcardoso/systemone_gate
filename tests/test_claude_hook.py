import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from systemone_gate.claude_hook import run_pretooluse

ROOT = Path(__file__).resolve().parent.parent
WARNING = "[SystemOne Gate] Warning: invalid payload, command not checked."


def payload(tool="Bash", **tool_input):
    return json.dumps({"tool_name": tool, "tool_input": tool_input})


def test_blocks_catastrophic_bash_command():
    code, msg = run_pretooluse(payload(command="rm -rf /"))
    assert code == 2
    assert msg.startswith("[SystemOne Gate] Command blocked: ")
    assert "(rule rm-recursive-root)" in msg


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


def _run_cli(stdin_text, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, "-m", "systemone_gate.cli", "hook-guard"],
        input=stdin_text, capture_output=True, text=True, cwd=str(ROOT), timeout=30, env=env,
    )


def test_cli_hook_guard_blocks_with_exit_2():
    proc = _run_cli(payload(command="ls && rm -rf /"))
    assert proc.returncode == 2
    assert "Command blocked" in proc.stderr


def test_cli_hook_guard_allows_with_exit_0():
    proc = _run_cli(payload(command="ls -la"))
    assert proc.returncode == 0
    assert proc.stderr == ""


def test_cli_hook_guard_invalid_json_warns_and_exits_0():
    proc = _run_cli("garbage")
    assert proc.returncode == 0
    assert "invalid payload" in proc.stderr


@pytest.mark.parametrize("env", [
    {"SYSTEMONE_TIMEOUT": "abc"},
    {"OLLAMA_SYSTEMONE_URL": "ftp://localhost/x"},
    {"OLLAMA_SYSTEMONE_URL": "http://example.com/v1/systemone"},
])
def test_cli_hook_guard_ignores_invalid_ollama_config(env):
    """hook-guard is offline: a bad Ollama setting must not turn into exit 2 (which blocks every Bash call)."""
    env = {**env, "SYSTEMONE_ALLOW_REMOTE": ""}
    allowed = _run_cli(payload(command="ls -la"), env)
    assert (allowed.returncode, allowed.stderr) == (0, "")
    blocked = _run_cli(payload(command="rm -rf /"), env)
    assert blocked.returncode == 2
    assert "Command blocked" in blocked.stderr
