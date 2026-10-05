import json
import queue
import subprocess
import sys
import threading

import pytest

TIMEOUT = 10


class McpProc:
    def __init__(self, url, env, cwd):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "systemone_gate.mcp_server"], cwd=cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self._q = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self._n = 0

    def _pump(self):
        for line in self.proc.stdout:
            self._q.put(line)
        self._q.put(None)

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._n += 1
            msg["id"] = self._n
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def recv(self, timeout=TIMEOUT):
        try:
            line = self._q.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError("MCP server did not reply within %ss" % timeout)
        assert line is not None, "MCP server closed stdout: " + self.proc.stderr.read()
        return json.loads(line)

    def call(self, method, params=None):
        self.send(method, params)
        return self.recv()

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
            self.proc.wait()
        finally:
            self.proc.stdout.close()
            self.proc.stderr.close()


@pytest.fixture
def mcp(fake, sub_env, repo_root):
    p = McpProc(fake.url, sub_env(fake.url), repo_root)
    yield p
    p.close()


def test_initialize(mcp):
    res = mcp.call("initialize", {})["result"]
    assert res["protocolVersion"] == "2024-11-05"
    assert res["serverInfo"]["name"] == "systemone-gate"
    assert "tools" in res["capabilities"]


def test_ping(mcp):
    msg = mcp.call("ping")
    assert msg["id"] == 1 and msg["result"] == {}


def test_tools_list(mcp):
    tools = mcp.call("tools/list")["result"]["tools"]
    assert sorted(t["name"] for t in tools) == sorted([
        "systemone_triage_error", "systemone_review_diff",
        "systemone_command_guard", "systemone_query"])
    for t in tools:
        schema = t["inputSchema"]
        assert schema["type"] == "object"
        assert t["description"]
        assert set(schema["required"]) <= set(schema["properties"])


def test_unknown_method_returns_32601(mcp):
    msg = mcp.call("does/not/exist")
    assert msg["error"]["code"] == -32601
    assert msg["id"] == 1


def test_initialized_notification_has_no_reply(mcp):
    mcp.send("notifications/initialized", notify=True)
    # next reply must belong to the ping, proving nothing was emitted before it
    msg = mcp.call("ping")
    assert msg["id"] == 1 and msg["result"] == {}


def _text_json(msg):
    return json.loads(msg["result"]["content"][0]["text"])


def test_tools_call_query(mcp, fake):
    fake.respond("nimble", {"answers": {"q": {"choice": "a"}}, "marker": "query"})
    qs = {"q": {"type": "choice", "instructions": "i", "criteria": {"a": None, "b": None}}}
    msg = mcp.call("tools/call", {"name": "systemone_query", "arguments": {"state": "st", "questions": qs}})
    assert _text_json(msg) == {"answers": {"q": {"choice": "a"}}, "marker": "query"}
    assert fake.requests[0]["state"] == "st" and fake.requests[0]["questions"] == qs


def test_tools_call_triage(mcp, fake):
    fake.respond("nimble", {"answers": {"root_cause": {"choice": "compilation_syntax"}}})
    msg = mcp.call("tools/call", {"name": "systemone_triage_error", "arguments": {"error_log": "oops"}})
    assert _text_json(msg) == {"answers": {"root_cause": {"choice": "compilation_syntax"}}}
    assert fake.requests[0]["state"] == "oops"
