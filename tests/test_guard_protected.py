import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from systemone_gate.claude_hook import run_pretooluse
from systemone_gate.client import SystemOneClient
from systemone_gate.guard_protected import ENV_VAR, load_protected_paths
from systemone_gate.guard_rules import evaluate_command

HOME = "/home/tester"
CWD = "/home/tester/work"
ENV = {"HOME": HOME, ENV_VAR: "~/Projetos:/srv/dados:$HOME/.ssh"}
ROOT = Path(__file__).resolve().parent.parent


def _protected(env=None):
    protected, _ = load_protected_paths(env or ENV, CWD)
    return protected


def _eval(command):
    return evaluate_command(command, protected=_protected())


MUST_BLOCK = [
    # the path itself, every spelling of home
    "rm -rf ~/Projetos",
    "rm -rf ~/Projetos/",
    "rm -rf $HOME/Projetos",
    "rm -rf ${HOME}/Projetos",
    'rm -rf "$HOME/Projetos"',
    "rm -rf /home/tester/Projetos",
    "rm -rf /home/tester/./Projetos",
    "rm -rf /home/tester/work/../Projetos",
    "rm -r -f ~/Projetos",
    "rm --recursive ~/Projetos",
    # contents and non-recursive removal of a protected path
    "rm -rf ~/Projetos/*",
    "rm ~/Projetos/*",
    "rm /srv/dados",
    "rm -f ~/.ssh",
    # ancestors of a protected path, when recursive
    "rm -rf /home/tester",
    "rm -rf /home/tester/*",
    "rm -rf ..",
    # globs that can match a protected path
    "rm -rf ~/Proj*",
    "rm -rf ~/P?ojetos",
    "rm -rf /srv/da*",
    "rm -rf /home/*/Projetos",
    # relative to cwd
    "cd /tmp && rm -rf /srv/dados",
    # wrappers, chains and nested shells
    "sudo rm -rf ~/Projetos",
    "ls && rm -rf ~/Projetos",
    'bash -c "rm -rf ~/Projetos"',
    "echo $(rm -rf ~/Projetos)",
    # mv moves the source away
    "mv ~/Projetos /tmp/old",
    "mv -f ~/Projetos /tmp/old",
    "mv ~/Projetos /tmp",
    "mv /tmp/a ~/Projetos /tmp/old",
    "mv ~/* /tmp/old",
    "mv /home/tester /tmp/old",
    # shred and recursive chmod/chown
    "shred -u /srv/dados",
    "chmod -R 000 ~/Projetos",
    "chown -R root /srv/dados",
    "chmod -R 755 /home/tester",
]

MUST_NOT_BLOCK = [
    # below the protected path
    "rm -rf ~/Projetos/x/build",
    "rm -rf ~/Projetos/x",
    "rm ~/Projetos/file.txt",
    "mv ~/Projetos/a ~/Projetos/b",
    "chmod -R 755 ~/Projetos/x",
    # siblings and look-alikes
    "rm -rf ~/projetos",
    "rm -rf ~/Projetos2",
    "rm -rf ~/Projetos-old",
    "rm -rf /srv/outros",
    "rm -rf ./build",
    "rm -rf node_modules",
    # non-recursive ops on an ancestor cannot remove the protected directory
    "rm /home/tester/*",
    "rm /home/tester/notes.txt",
    "chmod 644 /srv",
    # writing INTO a protected path is not removing it
    "mv /tmp/a ~/Projetos",
    "mv /tmp/a /srv/dados",
    "cp -r /tmp/a ~/Projetos",
    # read-only and unrelated commands
    "ls ~/Projetos",
    "cat /srv/dados/x",
    "echo ~/Projetos",
    'echo "rm -rf ~/Projetos"',
    "git status",
    # unresolvable expansions are not guessed
    "rm -rf $OTHER/Projetos",
    "rm -rf ~other/Projetos",
    "rm -rf `pwd`/Projetos",
]


@pytest.mark.parametrize("command", MUST_BLOCK)
def test_blocks_protected_path(command):
    match = _eval(command)
    assert match is not None, command
    assert match.rule_id == "protected-path"
    assert match.reason


@pytest.mark.parametrize("command", MUST_NOT_BLOCK)
def test_allows_outside_protected_path(command):
    assert _eval(command) is None, command


def test_relative_target_resolves_against_cwd():
    protected, _ = load_protected_paths({"HOME": HOME, ENV_VAR: "/home/tester/work/keep"}, CWD)
    assert evaluate_command("rm -rf keep", protected=protected).rule_id == "protected-path"
    assert evaluate_command("rm -rf ./keep/", protected=protected).rule_id == "protected-path"
    assert evaluate_command("rm -rf ../work/keep", protected=protected).rule_id == "protected-path"
    assert evaluate_command("rm -rf keep/sub", protected=protected) is None


def test_ancestor_outside_system_dirs_is_blocked_when_recursive():
    protected, _ = load_protected_paths({"HOME": HOME, ENV_VAR: "/data/a/b"}, CWD)
    assert evaluate_command("rm -rf /data/a", protected=protected).rule_id == "protected-path"
    assert evaluate_command("rm -rf /data/a/*", protected=protected).rule_id == "protected-path"
    assert evaluate_command("rm /data/a", protected=protected) is None
    assert evaluate_command("rm -rf /data/c", protected=protected) is None


def test_relative_target_is_ignored_without_cwd():
    protected, _ = load_protected_paths({"HOME": HOME, ENV_VAR: "/home/tester/work/keep"}, None)
    assert evaluate_command("rm -rf keep", protected=protected) is None
    assert evaluate_command("rm -rf /home/tester/work/keep", protected=protected).rule_id == "protected-path"


def test_removing_the_cwd_when_it_is_protected():
    protected, _ = load_protected_paths({"HOME": HOME, ENV_VAR: CWD}, CWD)
    assert evaluate_command("rm -rf .", protected=protected).rule_id == "protected-path"


def test_builtin_rules_keep_precedence_and_ids():
    assert _eval("rm -rf ~").rule_id == "rm-recursive-home"
    assert _eval("rm -rf /").rule_id == "rm-recursive-root"


def test_reason_names_the_protected_path():
    assert "/home/tester/Projetos" in _eval("rm -rf ~/Projetos").reason


def test_without_config_nothing_changes():
    assert evaluate_command("rm -rf ~/Projetos") is None
    assert evaluate_command("rm -rf ~/Projetos", protected=None) is None
    assert load_protected_paths({"HOME": HOME}, CWD) == (None, [])
    assert load_protected_paths({"HOME": HOME, ENV_VAR: ""}, CWD) == (None, [])
    assert load_protected_paths({"HOME": HOME, ENV_VAR: " : :"}, CWD) == (None, [])


def test_invalid_entries_are_skipped_with_a_warning():
    env = {"HOME": HOME, ENV_VAR: "relative/dir:$OTHER/x:~other/x:/srv/ok"}
    protected, warnings = load_protected_paths(env, CWD)
    assert len(warnings) == 3
    assert all(ENV_VAR in w for w in warnings)
    assert evaluate_command("rm -rf /srv/ok", protected=protected).rule_id == "protected-path"
    assert evaluate_command("rm -rf relative/dir", protected=protected) is None


def test_all_entries_invalid_yields_no_protection():
    protected, warnings = load_protected_paths({"HOME": HOME, ENV_VAR: "relative"}, CWD)
    assert protected is None
    assert len(warnings) == 1


def test_trailing_slashes_and_dots_in_entries_are_normalized():
    protected, warnings = load_protected_paths({"HOME": HOME, ENV_VAR: "/srv//dados/./"}, CWD)
    assert warnings == []
    assert evaluate_command("rm -rf /srv/dados", protected=protected).rule_id == "protected-path"


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf " + "~/Projetos " * 50_000,
        "rm -rf " + "*" * 100_000,
        "rm -rf ~/" + "../" * 50_000,
        "mv",
        "mv -t",
        "rm -rf ''",
    ],
)
def test_pathological_input_never_raises(command):
    evaluate_command(command, protected=_protected())


# --- claude hook ---------------------------------------------------------------------------------


def _payload(command, cwd=None):
    body = {"tool_name": "Bash", "tool_input": {"command": command}}
    if cwd is not None:
        body["cwd"] = cwd
    return json.dumps(body)


def test_hook_blocks_protected_path_with_exit_2():
    code, msg = run_pretooluse(_payload("rm -rf ~/Projetos"), env=ENV)
    assert code == 2
    assert "(rule protected-path)" in msg


def test_hook_uses_payload_cwd_for_relative_targets():
    env = {"HOME": HOME, ENV_VAR: "/srv/dados"}
    assert run_pretooluse(_payload("rm -rf dados", cwd="/srv"), env=env)[0] == 2
    assert run_pretooluse(_payload("rm -rf dados", cwd="/tmp"), env=env) == (0, "")


def test_hook_allows_when_unset():
    assert run_pretooluse(_payload("rm -rf ~/Projetos"), env={"HOME": HOME}) == (0, "")


def test_hook_invalid_config_warns_but_never_blocks():
    env = {"HOME": HOME, ENV_VAR: "relative"}
    code, msg = run_pretooluse(_payload("ls -la"), env=env)
    assert code == 0
    assert ENV_VAR in msg


def test_hook_block_message_keeps_config_warnings():
    env = {"HOME": HOME, ENV_VAR: "relative:/srv/dados"}
    code, msg = run_pretooluse(_payload("rm -rf /srv/dados"), env=env)
    assert code == 2
    assert "(rule protected-path)" in msg
    assert ENV_VAR in msg


def test_cli_hook_guard_reads_env_var():
    env = {**os.environ, "HOME": HOME, ENV_VAR: "/srv/dados"}
    proc = subprocess.run(
        [sys.executable, "-m", "systemone_gate.cli", "hook-guard"],
        input=_payload("rm -rf /srv/dados"),
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=30,
        env=env,
    )
    assert proc.returncode == 2
    assert "protected-path" in proc.stderr


# --- client / MCP path ---------------------------------------------------------------------------


def test_guard_command_short_circuits_on_protected_path(monkeypatch):
    monkeypatch.setenv("HOME", HOME)
    monkeypatch.setenv(ENV_VAR, "/srv/dados")
    result = SystemOneClient(endpoint="http://127.0.0.1:9/").guard_command("rm -rf /srv/dados")
    assert result["source"] == "rules"
    assert result["rule"]["id"] == "protected-path"
    assert result["answers"]["danger_score"]["score"] == 2.0


def test_guard_command_ignores_protection_when_unset(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    result = SystemOneClient(endpoint="http://127.0.0.1:9/").guard_command("rm -rf /srv/dados")
    assert "error" in result  # fell through to the (unreachable) model
