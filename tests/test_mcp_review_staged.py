"""systemone_review_staged: the server runs `git diff --cached` itself (DT-04)."""

import json
import os
import subprocess

import pytest
from test_mcp_protocol import McpProc

from systemone_gate import mcp_server

TOOL = "systemone_review_staged"
VERDICT = {"answers": {
    "risk_level": {"score": 0.5},
    "breaking_change": {"choice": "safe", "probabilities": {"safe": 0.9, "breaking_change": 0.1}},
}}


_GIT_ISOLATION = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def _git_env():
    env = dict(os.environ)
    env.update(_GIT_ISOLATION)
    return env


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, env=_git_env(), check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    return path


@pytest.fixture
def start(fake, sub_env):
    procs = []

    def make(cwd, url=None, extra_env=None):
        env = sub_env(url or fake.url)
        env.update(_GIT_ISOLATION)  # only git isolation: must not override the fake server URL
        env.update(extra_env or {})
        p = McpProc(url or fake.url, env, str(cwd))
        procs.append(p)
        return p

    yield make
    for p in procs:
        p.close()


def _call(proc, arguments=None):
    msg = proc.call("tools/call", {"name": TOOL, "arguments": arguments or {}})
    result = msg["result"]
    return result, json.loads(result["content"][0]["text"])


def _stage(repo, files):
    for name, text in files.items():
        (repo / name).write_text(text)
    _git(repo, "add", "-A")


def test_only_code_file_reaches_model_and_lockfile_is_skipped(repo, start, fake):
    fake.respond("nimble", VERDICT)
    _stage(repo, {"app.py": "print('hi')\n", "package-lock.json": "{}\n"})
    result, payload = _call(start(repo))
    assert "isError" not in result
    assert len(fake.requests) == 1
    assert "app.py" in fake.requests[0]["state"]
    assert "package-lock.json" not in fake.requests[0]["state"]
    assert payload["coverage"]["reviewed"] == ["app.py"]
    assert payload["coverage"]["skipped"] == [{"path": "package-lock.json", "reason": "lockfile"}]
    assert payload["answers"]["risk_level"]["score"] == 0.5
    assert payload["decision"]["action"] == "allow"
    assert payload["decision"]["reasons"] == []


def test_decision_blocks_when_risk_exceeds_threshold(repo, start, fake):
    fake.respond("nimble", {"answers": {
        "risk_level": {"score": 1.9},
        "breaking_change": {"choice": "breaking_change", "probabilities": {"breaking_change": 0.99}},
    }})
    _stage(repo, {"app.py": "x = 1\n"})
    _, payload = _call(start(repo))
    assert payload["decision"]["action"] == "block"
    assert payload["decision"]["reasons"]


def test_nothing_staged_is_a_note_not_an_error(repo, start, fake):
    result, payload = _call(start(repo))
    assert "isError" not in result
    assert payload["note"] == "no staged changes"
    assert payload["coverage"] == {"reviewed": [], "skipped": [], "truncated": []}
    assert fake.requests == []


def test_model_argument_is_forwarded(repo, start, fake):
    fake.respond("tev1:0.8b", VERDICT)
    _stage(repo, {"app.py": "x = 1\n"})
    _call(start(repo), {"model": "tev1:0.8b"})
    assert fake.requests[0]["model"] == "tev1:0.8b"


def test_default_model_is_nimble(repo, start, fake):
    _stage(repo, {"app.py": "x = 1\n"})
    _call(start(repo))
    assert fake.requests[0]["model"] == "nimble"


def test_outside_a_repo_is_error_and_server_keeps_running(tmp_path, start):
    outside = tmp_path / "plain"
    outside.mkdir()
    proc = start(outside, extra_env={"GIT_CEILING_DIRECTORIES": str(tmp_path)})
    result, payload = _call(proc)
    assert result["isError"] is True
    assert "git" in payload["error"].lower()
    assert "Traceback" not in payload["error"]
    assert proc.call("ping")["result"] == {}


def test_closed_port_is_error(repo, start, closed_port_url):
    _stage(repo, {"app.py": "x = 1\n"})
    result, payload = _call(start(repo, url=closed_port_url))
    assert result["isError"] is True
    assert "error" in payload


def test_invalid_policy_env_is_error_payload(repo, start, fake):
    fake.respond("nimble", VERDICT)
    _stage(repo, {"app.py": "x = 1\n"})
    result, payload = _call(start(repo, extra_env={"SYSTEMONE_DIFF_ON_ERROR": "bogus"}))
    assert result["isError"] is True
    assert "SYSTEMONE_DIFF_ON_ERROR" in payload["error"]


def test_tools_list_exposes_review_staged(repo, start):
    tools = start(repo).call("tools/list")["result"]["tools"]
    assert len(tools) == 5
    tool = next(t for t in tools if t["name"] == TOOL)
    assert tool["inputSchema"]["type"] == "object"
    assert "model" in tool["inputSchema"]["properties"]
    assert not tool["inputSchema"].get("required")


@pytest.mark.parametrize("name", ["systemone_review_diff", "systemone_triage_error"])
def test_old_tools_prefer_review_staged(name):
    tool = next(t for t in mcp_server.MCP_TOOLS if t["name"] == name)
    assert TOOL in tool["description"]


# --- in-process helper tests (subprocess.run monkeypatched) ---------------

def _fake_run(returncode=0, stdout="", stderr="", exc=None):
    def run(cmd, **kwargs):
        run.calls.append((cmd, kwargs))
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
    run.calls = []
    return run


def test_staged_diff_uses_arg_list_no_shell_and_timeout(monkeypatch):
    run = _fake_run(stdout="diff --git a/x b/x\n")
    monkeypatch.setattr(mcp_server.subprocess, "run", run)
    assert mcp_server._staged_diff() == {"diff": "diff --git a/x b/x\n"}
    cmd, kwargs = run.calls[0]
    assert cmd == ["git", "diff", "--cached"]
    assert kwargs["timeout"] == mcp_server.GIT_DIFF_TIMEOUT_SECONDS
    assert kwargs["capture_output"] is True and kwargs["text"] is True
    assert not kwargs.get("shell")


@pytest.mark.parametrize("exc,fragment", [
    (subprocess.TimeoutExpired(["git"], 30), "timed out"),
    (FileNotFoundError("git"), "not found"),
    (OSError("boom"), "git"),
])
def test_staged_diff_maps_failures_to_errors(monkeypatch, exc, fragment):
    monkeypatch.setattr(mcp_server.subprocess, "run", _fake_run(exc=exc))
    out = mcp_server._staged_diff()
    assert fragment in out["error"]
    assert "Traceback" not in out["error"]


def test_staged_diff_nonzero_exit_is_error(monkeypatch):
    monkeypatch.setattr(mcp_server.subprocess, "run",
                        _fake_run(returncode=128, stderr="fatal: not a git repository\n"))
    out = mcp_server._staged_diff()
    assert "git repository" in out["error"]


def test_dispatch_review_staged_with_stub_client(monkeypatch):
    monkeypatch.setattr(mcp_server.subprocess, "run", _fake_run(stdout=""))
    result = mcp_server._dispatch_tool(object(), TOOL, {})
    assert "isError" not in result
    assert json.loads(result["content"][0]["text"])["note"] == "no staged changes"


class _VerdictClient:
    def review_diff(self, text, model=None):
        return VERDICT


class _ErrorClient:
    def review_diff(self, text, model=None):
        return {"error": "Ollama indisponível"}


def _staged_stub(monkeypatch, diff="diff --git a/a.py b/a.py\n+x\n"):
    monkeypatch.setattr(mcp_server, "_staged_diff", lambda: {"diff": diff})


def test_in_process_success_has_decision(monkeypatch):
    _staged_stub(monkeypatch)
    out = mcp_server._review_staged_tool(_VerdictClient(), {})
    assert out["decision"] == {"action": "allow", "reasons": [], "warning": None}
    assert out["coverage"]["reviewed"] == ["a.py"]


def test_in_process_model_error_is_propagated(monkeypatch):
    _staged_stub(monkeypatch)
    assert mcp_server._review_staged_tool(_ErrorClient(), {}) == {"error": "Ollama indisponível"}


def test_in_process_invalid_env(monkeypatch):
    _staged_stub(monkeypatch)
    monkeypatch.setenv("SYSTEMONE_DIFF_ON_ERROR", "bogus")
    out = mcp_server._review_staged_tool(_VerdictClient(), {})
    assert "SYSTEMONE_DIFF_ON_ERROR" in out["error"]


def test_in_process_git_error_is_propagated(monkeypatch):
    monkeypatch.setattr(mcp_server, "_staged_diff", lambda: {"error": "x"})
    assert mcp_server._review_staged_tool(_VerdictClient(), {}) == {"error": "x"}


def test_in_process_nothing_staged(monkeypatch):
    _staged_stub(monkeypatch, diff="  \n")
    assert mcp_server._review_staged_tool(_VerdictClient(), {})["note"] == "no staged changes"
