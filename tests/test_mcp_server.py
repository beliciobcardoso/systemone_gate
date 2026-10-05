"""Tests for the stdio MCP server: hostile input must never kill the loop."""

import json
import os
import queue
import subprocess
import sys
import threading

import pytest

from systemone_gate import mcp_server

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 15
TOOLS = [
    "systemone_triage_error",
    "systemone_review_diff",
    "systemone_command_guard",
    "systemone_query",
]


class ServerProc:
    def __init__(self):
        env = dict(os.environ, OLLAMA_SYSTEMONE_URL="http://127.0.0.1:9/")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "systemone_gate.mcp_server"],
            cwd=REPO_ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.lines = queue.Queue()
        self.stdout_seen = []
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self.proc.stderr.read, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def send_raw(self, text):
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()

    def send(self, obj):
        self.send_raw(json.dumps(obj))

    def recv(self):
        try:
            line = self.lines.get(timeout=TIMEOUT)
        except queue.Empty:
            raise AssertionError("server hung: no reply within timeout")
        assert line is not None, "server died (stdout closed)"
        self.stdout_seen.append(line)
        return json.loads(line)

    def assert_alive(self, req_id=999):
        self.send({"jsonrpc": "2.0", "id": req_id, "method": "ping"})
        reply = self.recv()
        assert reply["id"] == req_id
        assert reply["result"] == {}
        assert self.proc.poll() is None

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=TIMEOUT)
        except Exception:
            self.proc.kill()


@pytest.fixture
def server():
    srv = ServerProc()
    yield srv
    srv.close()
    for line in srv.stdout_seen:
        assert json.loads(line)["jsonrpc"] == "2.0"


def call(tool, arguments, req_id=2):
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }


@pytest.mark.parametrize("tool", TOOLS)
def test_arguments_null_is_treated_as_empty(server, tool):
    server.send(call(tool, None))
    reply = server.recv()
    assert reply["id"] == 2
    assert "error" not in reply
    assert "content" in reply["result"]
    server.assert_alive()


@pytest.mark.parametrize("tool", TOOLS)
def test_arguments_missing_is_treated_as_empty(server, tool):
    server.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": tool}})
    reply = server.recv()
    assert reply["id"] == 2
    assert "error" not in reply
    assert "content" in reply["result"]
    server.assert_alive()


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("bad", ["text", ["a"], 7])
def test_arguments_non_object_is_invalid_params(server, tool, bad):
    server.send(call(tool, bad))
    reply = server.recv()
    assert reply["id"] == 2
    assert reply["error"]["code"] == -32602
    assert isinstance(reply["error"]["message"], str)
    server.assert_alive()


def test_params_null_does_not_crash(server):
    server.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": None})
    reply = server.recv()
    assert reply["id"] == 2
    server.assert_alive()


def test_params_missing_does_not_crash(server):
    server.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call"})
    reply = server.recv()
    assert reply["id"] == 2
    server.assert_alive()


@pytest.mark.parametrize("raw", ["[]", "1", '"str"', "null", "true"])
def test_non_object_json_line_is_ignored(server, raw):
    server.send_raw(raw)
    server.assert_alive()


def test_unknown_tool_returns_result_and_keeps_running(server):
    server.send(call("nope", {}))
    reply = server.recv()
    assert reply["id"] == 2
    assert "content" in reply["result"]
    server.assert_alive()


def test_stdout_only_contains_jsonrpc_lines(server):
    server.send(call("systemone_command_guard", None))
    server.recv()
    server.send_raw("[]")
    server.assert_alive()
    assert server.stdout_seen
    for line in server.stdout_seen:
        assert json.loads(line)["jsonrpc"] == "2.0"


class _ExplodingClient:
    def guard_command(self, command):
        raise RuntimeError("secret-internal-detail")


def test_dispatch_unexpected_exception_is_generic_iserror(capsys):
    result = mcp_server._dispatch_tool(
        _ExplodingClient(), "systemone_command_guard", {"command": "ls"}
    )
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "secret-internal-detail" not in text
    assert "RuntimeError" not in text
    assert "Internal error" in text
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "secret-internal-detail" in captured.err


class _StubClient:
    def triage_error(self, error_log, model=None):
        return {"ok": "triage", "log": error_log, "model": model}

    def review_diff(self, diff, model=None):
        return {"ok": "review"}

    def guard_command(self, command):
        return {"ok": "guard"}

    def evaluate(self, state, questions, model=None):
        return {"ok": "query"}


@pytest.mark.parametrize("tool,marker", [
    ("systemone_triage_error", "triage"),
    ("systemone_review_diff", "review"),
    ("systemone_command_guard", "guard"),
    ("systemone_query", "query"),
])
def test_dispatch_routes_each_tool(tool, marker):
    result = mcp_server._dispatch_tool(_StubClient(), tool, {})
    assert "isError" not in result
    assert json.loads(result["content"][0]["text"])["ok"] == marker


def test_dispatch_does_not_mutate_arguments():
    args = {"error_log": "boom"}
    mcp_server._dispatch_tool(_StubClient(), "systemone_triage_error", args)
    assert args == {"error_log": "boom"}


def test_run_loop_in_process_covers_all_methods(monkeypatch, capsys):
    import io

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "systemone_command_guard", "arguments": None}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "systemone_command_guard", "arguments": "x"}},
        {"jsonrpc": "2.0", "id": 6, "method": "bogus"},
        {"jsonrpc": "2.0", "method": "bogus"},
    ]
    raw = "\n".join(
        ["", "not json", "[]"] + [json.dumps(r) for r in requests]
    ) + "\n"
    monkeypatch.setattr(mcp_server, "SystemOneClient", _StubClient)
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))

    mcp_server.run_mcp_server()

    replies = [json.loads(l) for l in capsys.readouterr().out.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5, 6]
    assert replies[0]["result"]["protocolVersion"] == "2024-11-05"
    assert replies[3]["result"]["content"]
    assert replies[4]["error"]["code"] == -32602
    assert replies[5]["error"]["code"] == -32601
