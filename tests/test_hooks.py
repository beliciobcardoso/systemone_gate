import os
import stat
import subprocess
import sys

import pytest

from systemone_gate import hooks

WORKTREE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK_RAN_MARKER = "Nenhuma alteração staged"
WARNING = "[SystemOne Gate] pacote indisponível, pulando verificação."


def _git_env():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["OLLAMA_SYSTEMONE_URL"] = "http://127.0.0.1:9/"
    return env


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    env = _git_env()
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.t"], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"], check=True, env=env)
    return path


def hook_path(repo, name="pre-commit"):
    return repo / ".git" / "hooks" / name


def run_hook(repo, pythonpath=None):
    env = _git_env()
    if pythonpath is not None:
        env["PYTHONPATH"] = pythonpath
    return subprocess.run(
        [str(hook_path(repo))], cwd=repo, env=env, capture_output=True, text=True
    )


def write_foreign(repo, body, name="pre-commit", mode=0o755):
    path = hook_path(repo, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    os.chmod(path, mode)
    return path


def mode_of(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_fresh_install_creates_executable_hook_with_absolute_python(repo):
    assert hooks.install_git_hook(str(repo)) is True
    path = hook_path(repo)
    assert os.access(path, os.X_OK)
    assert mode_of(path) == 0o755
    content = path.read_text(encoding="utf-8")
    assert sys.executable in content
    assert "SystemOne Gate" in content


def test_hook_skips_with_warning_when_package_unavailable(repo):
    hooks.install_git_hook(str(repo))
    result = run_hook(repo)
    assert result.returncode == 0
    assert WARNING in result.stderr


def test_hook_runs_check_when_package_available(repo):
    hooks.install_git_hook(str(repo))
    result = run_hook(repo, pythonpath=WORKTREE_ROOT)
    assert result.returncode == 0
    assert CHECK_RAN_MARKER in result.stdout
    assert WARNING not in result.stderr


def test_foreign_hook_is_backed_up_and_failure_blocks_without_running_check(repo):
    write_foreign(repo, "#!/bin/sh\necho foreign-ran\nexit 1\n")
    assert hooks.install_git_hook(str(repo)) is True
    assert hook_path(repo, "pre-commit.backup").exists()
    result = run_hook(repo, pythonpath=WORKTREE_ROOT)
    assert result.returncode == 1
    assert "foreign-ran" in result.stdout
    assert CHECK_RAN_MARKER not in result.stdout


def test_foreign_hook_success_lets_check_run(repo):
    write_foreign(repo, "#!/bin/sh\necho foreign-ran\nexit 0\n")
    hooks.install_git_hook(str(repo))
    result = run_hook(repo, pythonpath=WORKTREE_ROOT)
    assert result.returncode == 0
    assert "foreign-ran" in result.stdout
    assert CHECK_RAN_MARKER in result.stdout


def test_foreign_backup_exit_code_is_propagated(repo):
    write_foreign(repo, "#!/bin/sh\nexit 7\n")
    hooks.install_git_hook(str(repo))
    assert run_hook(repo, pythonpath=WORKTREE_ROOT).returncode == 7


def test_non_executable_backup_is_ignored(repo):
    write_foreign(repo, "#!/bin/sh\nexit 1\n", mode=0o644)
    hooks.install_git_hook(str(repo))
    result = run_hook(repo, pythonpath=WORKTREE_ROOT)
    assert result.returncode == 0
    assert CHECK_RAN_MARKER in result.stdout


def test_reinstall_is_idempotent_and_keeps_backup(repo):
    original = "#!/bin/sh\necho foreign\n"
    write_foreign(repo, original)
    hooks.install_git_hook(str(repo))
    first = hook_path(repo).read_text(encoding="utf-8")
    assert hooks.install_git_hook(str(repo)) is True
    assert hook_path(repo).read_text(encoding="utf-8") == first
    backup = hook_path(repo, "pre-commit.backup")
    assert backup.read_text(encoding="utf-8") == original
    assert sorted(
        p.name for p in hook_path(repo).parent.glob("pre-commit*") if p.suffix != ".sample"
    ) == [
        "pre-commit",
        "pre-commit.backup",
    ]


def test_install_refuses_foreign_hook_when_backup_exists(repo, capsys):
    write_foreign(repo, "#!/bin/sh\necho old-backup\n", name="pre-commit.backup")
    foreign = write_foreign(repo, "#!/bin/sh\necho new-foreign\n")
    capsys.readouterr()
    assert hooks.install_git_hook(str(repo)) is False
    assert foreign.read_text(encoding="utf-8") == "#!/bin/sh\necho new-foreign\n"
    assert "old-backup" in hook_path(repo, "pre-commit.backup").read_text(encoding="utf-8")
    assert "backup" in capsys.readouterr().err


def test_install_outside_git_repo_returns_false(tmp_path, capsys):
    assert hooks.install_git_hook(str(tmp_path)) is False
    assert capsys.readouterr().err


def test_uninstall_restores_backup_byte_for_byte_and_mode(repo):
    original = b"#!/bin/sh\necho \xc3\xa9 foreign\n"
    path = hook_path(repo)
    path.write_bytes(original)
    os.chmod(path, 0o750)
    hooks.install_git_hook(str(repo))
    assert hooks.uninstall_git_hook(str(repo)) is True
    assert path.read_bytes() == original
    assert mode_of(path) == 0o750
    assert not hook_path(repo, "pre-commit.backup").exists()


def test_uninstall_removes_ours_when_no_backup(repo):
    hooks.install_git_hook(str(repo))
    assert hooks.uninstall_git_hook(str(repo)) is True
    assert not hook_path(repo).exists()


def test_uninstall_refuses_foreign_hook(repo, capsys):
    foreign = write_foreign(repo, "#!/bin/sh\necho mine\n")
    capsys.readouterr()
    assert hooks.uninstall_git_hook(str(repo)) is False
    assert foreign.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"
    assert capsys.readouterr().err


def test_uninstall_missing_hook_warns_and_returns_false(repo, capsys):
    assert hooks.uninstall_git_hook(str(repo)) is False
    assert "não encontrado" in capsys.readouterr().out


def test_uninstall_outside_git_repo_returns_false(tmp_path):
    assert hooks.uninstall_git_hook(str(tmp_path)) is False
