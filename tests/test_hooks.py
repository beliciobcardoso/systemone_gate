import os
import stat
import subprocess
import sys

import pytest

from systemone_gate import hooks

WORKTREE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK_RAN_MARKER = "No staged changes"
WARNING = "[SystemOne Gate] package unavailable, skipping check."


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


@pytest.fixture
def package_unavailable(tmp_path, monkeypatch):
    """Make the hook's interpreter unable to import systemone_gate, whatever the test environment is.

    The hook embeds sys.executable at install time. Relying on the real interpreter lacking the package
    only works when the project is not pip-installed; with `pip install -e ".[dev]"` (the documented
    setup) the import would succeed everywhere.
    """
    stub = tmp_path / "python-without-package"
    stub.write_text("#!/bin/sh\nexit 1\n")
    stub.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(stub))
    return stub


def hook_path(repo, name="pre-commit"):
    return repo / ".git" / "hooks" / name


def run_hook(repo, pythonpath=None, extra_env=None):
    env = _git_env()
    env.update(extra_env or {})
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


def test_hook_skips_with_warning_when_package_unavailable(repo, package_unavailable):
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
    assert "not found" in capsys.readouterr().out


def test_uninstall_outside_git_repo_returns_false(tmp_path):
    assert hooks.uninstall_git_hook(str(tmp_path)) is False


# --- DEF-05 / DEF-09: hooks directory resolved by git itself ---

def git(cwd, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], env=_git_env(), capture_output=True, text=True, check=check
    )


def commit_all(cwd, message="c"):
    git(cwd, "add", "-A")
    return git(cwd, "commit", "-q", "-m", message, check=False)


@pytest.fixture
def committed_repo(repo):
    (repo / "a.txt").write_text("a", encoding="utf-8")
    assert commit_all(repo, "init").returncode == 0
    return repo


def test_install_from_linked_worktree_uses_shared_hooks_dir_and_hook_runs(
    committed_repo, tmp_path, package_unavailable
):
    wt = tmp_path / "wt"
    git(committed_repo, "worktree", "add", "-q", str(wt), "-b", "other")
    assert (wt / ".git").is_file()

    assert hooks.install_git_hook(str(wt)) is True

    shared = hook_path(committed_repo)
    assert shared.exists()
    (wt / "b.txt").write_text("b", encoding="utf-8")
    result = commit_all(wt, "from worktree")
    assert result.returncode == 0
    assert WARNING in result.stderr

    assert hooks.uninstall_git_hook(str(wt)) is True
    assert not shared.exists()


def test_install_from_submodule_where_dot_git_is_a_file(committed_repo, tmp_path):
    sub_src = tmp_path / "sub_src.git"
    subprocess.run(["git", "init", "-q", "--bare", str(sub_src)], check=True, env=_git_env())
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(sub_src), str(seed)], check=True, env=_git_env())
    git(seed, "config", "user.email", "t@t.t")
    git(seed, "config", "user.name", "T")
    (seed / "s.txt").write_text("s", encoding="utf-8")
    assert commit_all(seed, "seed").returncode == 0
    git(seed, "push", "-q", "origin", "HEAD")
    git(committed_repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub_src), "mod")
    sub = committed_repo / "mod"
    assert (sub / ".git").is_file()

    assert hooks.find_git_root(str(sub)) == str(sub)
    assert hooks.install_git_hook(str(sub)) is True

    hooks_dir = git(sub, "rev-parse", "--git-path", "hooks").stdout.strip()
    installed = os.path.join(str(sub), hooks_dir, "pre-commit")
    assert os.path.exists(installed)
    assert not hook_path(committed_repo).exists()
    assert hooks.uninstall_git_hook(str(sub)) is True
    assert not os.path.exists(installed)


def test_core_hooks_path_relative_installs_and_uninstalls_there(repo):
    git(repo, "config", "core.hooksPath", ".husky")
    original = "#!/bin/sh\necho husky\n"
    husky = repo / ".husky" / "pre-commit"
    husky.parent.mkdir()
    husky.write_text(original, encoding="utf-8")
    os.chmod(husky, 0o755)

    assert hooks.install_git_hook(str(repo)) is True
    assert "SystemOne Gate" in husky.read_text(encoding="utf-8")
    assert (repo / ".husky" / "pre-commit.backup").read_text(encoding="utf-8") == original
    assert not hook_path(repo).exists()

    assert hooks.uninstall_git_hook(str(repo)) is True
    assert husky.read_text(encoding="utf-8") == original
    assert not hook_path(repo).exists()


def test_core_hooks_path_relative_is_resolved_against_repo_root_from_subdir(repo):
    git(repo, "config", "core.hooksPath", ".husky")
    sub = repo / "pkg" / "deep"
    sub.mkdir(parents=True)

    assert hooks.install_git_hook(str(sub)) is True
    assert (repo / ".husky" / "pre-commit").exists()
    assert not (sub / ".husky").exists()


def test_core_hooks_path_absolute_outside_repo(repo, tmp_path):
    shared = tmp_path / "shared_hooks"
    git(repo, "config", "core.hooksPath", str(shared))

    assert hooks.install_git_hook(str(repo)) is True
    target = shared / "pre-commit"
    assert target.exists()
    assert not hook_path(repo).exists()

    assert hooks.uninstall_git_hook(str(repo)) is True
    assert not target.exists()


def test_install_outside_repo_prints_portuguese_error(tmp_path, capsys):
    assert hooks.install_git_hook(str(tmp_path)) is False
    assert ".git directory not found" in capsys.readouterr().err


def test_git_not_installed_returns_false_without_raising(repo, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(repo))
    assert hooks.install_git_hook(str(repo)) is False
    assert ".git directory not found" in capsys.readouterr().err
    assert hooks.uninstall_git_hook(str(repo)) is False


@pytest.mark.parametrize("exc", [FileNotFoundError(), subprocess.TimeoutExpired("git", 1), OSError("boom")])
def test_subprocess_failures_return_false(repo, monkeypatch, capsys, exc):
    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(hooks.subprocess, "run", boom)
    assert hooks.install_git_hook(str(repo)) is False
    assert ".git directory not found" in capsys.readouterr().err


def test_find_git_root_accepts_dot_git_file(tmp_path):
    root = tmp_path / "proj"
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    (root / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    assert hooks.find_git_root(str(nested)) == str(root)


def test_find_git_root_returns_none_outside_repo(tmp_path):
    assert hooks.find_git_root(str(tmp_path)) is None


def test_find_git_root_default_start_uses_cwd(repo, monkeypatch):
    monkeypatch.chdir(repo)
    assert hooks.find_git_root() == str(repo)


def test_install_defaults_to_cwd(repo, monkeypatch):
    monkeypatch.chdir(repo)
    assert hooks.install_git_hook() is True
    assert hook_path(repo).exists()


SKIP_WARNING = "[SystemOne Gate] check skipped (SYSTEMONE_SKIP)."


def test_skip_env_bypasses_only_our_check_with_warning(repo):
    hooks.install_git_hook(str(repo))
    result = run_hook(repo, pythonpath=WORKTREE_ROOT, extra_env={"SYSTEMONE_SKIP": "1"})
    assert result.returncode == 0
    assert SKIP_WARNING in result.stderr
    assert CHECK_RAN_MARKER not in result.stdout


def test_skip_runs_before_package_import_check(repo):
    hooks.install_git_hook(str(repo))
    result = run_hook(repo, extra_env={"SYSTEMONE_SKIP": "1"})
    assert SKIP_WARNING in result.stderr
    assert WARNING not in result.stderr


@pytest.mark.parametrize("value", ["0", ""])
def test_skip_env_zero_or_empty_does_not_skip(repo, value):
    hooks.install_git_hook(str(repo))
    result = run_hook(repo, pythonpath=WORKTREE_ROOT, extra_env={"SYSTEMONE_SKIP": value})
    assert result.returncode == 0
    assert SKIP_WARNING not in result.stderr
    assert CHECK_RAN_MARKER in result.stdout


def test_skip_does_not_bypass_failing_original_hook(repo):
    write_foreign(repo, "#!/bin/sh\necho foreign-ran\nexit 3\n")
    hooks.install_git_hook(str(repo))
    result = run_hook(repo, pythonpath=WORKTREE_ROOT, extra_env={"SYSTEMONE_SKIP": "1"})
    assert result.returncode == 3
    assert "foreign-ran" in result.stdout
    assert SKIP_WARNING not in result.stderr


def test_hint_targets_this_hook_only_and_never_mentions_no_verify():
    script = hooks.render_hook_script(sys.executable)
    assert "--no-verify" not in script
    assert "SYSTEMONE_SKIP=1 git commit" in script
    assert "ONLY this check" in script


# --- default timeout of the generated hook (nimble can take up to ~72 s to load after an idle period) ---


def _run_rendered_hook(tmp_path, extra_env=None):
    """Run the rendered hook with a stub interpreter that just echoes the timeout it receives."""
    stub = tmp_path / "py"
    stub.write_text('#!/bin/sh\nif [ "$1" = "-c" ]; then exit 0; fi\necho "TIMEOUT=${SYSTEMONE_TIMEOUT-unset}"\n')
    stub.chmod(0o755)
    script = tmp_path / "pre-commit"
    script.write_text(hooks.render_hook_script(str(stub)))
    script.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("SYSTEMONE_")}
    env.update(extra_env or {})
    return subprocess.run([str(script)], capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=30)


def test_hook_timeout_constant_covers_the_measured_cold_start():
    # measured nimble cold starts: 11.8 s, 46.5 s and 72.4 s; the CLI default (30 s) is not enough
    assert hooks.HOOK_TIMEOUT_SECONDS >= 100


def test_hook_exports_a_longer_default_timeout(tmp_path):
    result = _run_rendered_hook(tmp_path)
    assert result.returncode == 0
    assert f"TIMEOUT={hooks.HOOK_TIMEOUT_SECONDS}" in result.stdout


def test_hook_keeps_a_timeout_defined_by_the_user(tmp_path):
    result = _run_rendered_hook(tmp_path, {"SYSTEMONE_TIMEOUT": "7"})
    assert "TIMEOUT=7" in result.stdout


def test_rendered_hook_has_no_unresolved_placeholder():
    assert "@@" not in hooks.render_hook_script("/usr/bin/python3")
