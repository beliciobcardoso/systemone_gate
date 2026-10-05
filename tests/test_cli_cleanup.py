import os
import subprocess

import pytest

from systemone_gate import cli, diff_review, hooks
from systemone_gate.cli import main


@pytest.fixture
def repo(tmp_path, monkeypatch):
    path = tmp_path / "repo"
    path.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _exit_code(argv):
    with pytest.raises(SystemExit) as e:
        main(argv)
    return e.value.code


def test_uninstall_hook_exits_0_and_removes_installed_hook(repo):
    assert hooks.install_git_hook(str(repo)) is True
    assert _exit_code(["uninstall-hook", "--repo", str(repo)]) == 0
    assert not (repo / ".git" / "hooks" / "pre-commit").exists()


def test_uninstall_hook_exits_1_for_foreign_hook(repo):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    assert _exit_code(["uninstall-hook", "--repo", str(repo)]) == 1
    assert hook.exists()


def test_uninstall_hook_exits_1_outside_a_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert _exit_code(["uninstall-hook", "--repo", str(tmp_path)]) == 1


def test_diff_git_failure_by_timeout_keeps_error_path(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git diff --cached", timeout=1)

    monkeypatch.setattr(cli.subprocess, "check_output", boom)
    assert _exit_code(["diff"]) == 1
    assert "git diff --cached" in capsys.readouterr().err


def test_diff_passes_timeout_to_git(monkeypatch):
    seen = {}

    def fake(*args, **kwargs):
        seen.update(kwargs)
        return ""

    monkeypatch.setattr(cli.subprocess, "check_output", fake)
    assert _exit_code(["diff"]) == 0
    assert seen["timeout"] == cli.GIT_DIFF_TIMEOUT_SECONDS == 30


def test_default_caps_are_shared_named_constants():
    assert diff_review.DEFAULT_MAX_LINES_PER_FILE == 250
    assert diff_review.DEFAULT_MAX_FILES == 20
    assert cli.DEFAULT_MAX_LINES_PER_FILE is diff_review.DEFAULT_MAX_LINES_PER_FILE
