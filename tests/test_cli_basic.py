import json
import subprocess
import sys

import pytest

from systemone_gate.cli import main


def test_main_no_args_prints_help_and_exits_0(capsys):
    with pytest.raises(SystemExit) as e:
        main([])
    assert e.value.code == 0
    assert "usage: systemone-gate" in capsys.readouterr().out


def test_main_help_exits_0(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0
    assert "triage" in capsys.readouterr().out


def test_unknown_subcommand_exits_2(capsys):
    with pytest.raises(SystemExit) as e:
        main(["bogus"])
    assert e.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def _run(args, url, sub_env, repo_root):
    return subprocess.run([sys.executable, "-m", "systemone_gate.cli"] + args, cwd=repo_root,
                          env=sub_env(url), capture_output=True, text=True, timeout=30)


def test_triage_happy_path(fake, sub_env, repo_root):
    fake.respond("nimble", {"answers": {"root_cause": {"choice": "compilation_syntax"}}})
    out = _run(["triage", "error:", "boom"], fake.url, sub_env, repo_root)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout[out.stdout.index("{"):]) == {"root_cause": {"choice": "compilation_syntax"}}
    assert fake.requests[0]["state"] == "error: boom"
    assert fake.requests[0]["model"] == "nimble"


def test_triage_model_flag_forwarded(fake, sub_env, repo_root):
    out = _run(["triage", "x", "--model", "tev1:0.8b"], fake.url, sub_env, repo_root)
    assert out.returncode == 0, out.stderr
    assert fake.requests[0]["model"] == "tev1:0.8b"


def test_triage_connection_refused_exits_1_with_stderr(closed_port_url, sub_env, repo_root):
    out = _run(["triage", "boom"], closed_port_url, sub_env, repo_root)
    assert out.returncode == 1
    assert "Failed to connect" in out.stderr
