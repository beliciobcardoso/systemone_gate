import copy
import json
import subprocess
import sys

import pytest

from systemone_gate.client import SystemOneClient

QUESTIONS = {"q": {"type": "choice", "instructions": "x", "criteria": {"a": None, "b": None}}}


# ---------- DEF-06: HTTP errors ----------

def test_http_404_json_error_and_hint(fake, client):
    fake.status = 404
    fake.status_body = b'{"error": "model \'x\' not found"}'
    res = client(fake.url).evaluate("s", QUESTIONS, model="x")
    assert res["error_kind"] == "http" and res["status"] == 404
    assert res["model"] == "x"
    assert "404" in res["error"]
    assert "model 'x' not found" in res["error"]
    assert "endpoint /v1/systemone não encontrado (Ollama < 0.35?) ou modelo 'x' ausente" in res["error"]
    assert "Failed to connect" not in res["error"]


def test_http_404_non_json_body_uses_raw_snippet(fake, client):
    fake.status = 404
    fake.status_body = b"404 page not found"
    res = client(fake.url).evaluate("s", QUESTIONS)
    assert "404 page not found" in res["error"]
    assert "endpoint /v1/systemone não encontrado" in res["error"]
    assert res["error_kind"] == "http"


def test_http_400_includes_server_text_without_404_hint(fake, client):
    fake.status = 400
    fake.status_body = b'{"error": "bad questions"}'
    res = client(fake.url).evaluate("s", QUESTIONS)
    assert res["status"] == 400 and res["error_kind"] == "http"
    assert "400" in res["error"] and "bad questions" in res["error"]
    assert "não encontrado" not in res["error"]


def test_http_500_with_body(fake, client):
    fake.status = 500
    res = client(fake.url).evaluate("s", QUESTIONS)  # default fake body: {"error": "forced"}
    assert res["status"] == 500 and "forced" in res["error"]


def test_http_500_without_body(fake, client):
    fake.status = 500
    fake.status_body = b""
    res = client(fake.url).evaluate("s", QUESTIONS)
    assert res["status"] == 500 and res["error_kind"] == "http"
    assert "500" in res["error"] and "Failed to connect" not in res["error"]


def test_http_json_error_field_not_string_falls_back_to_raw(fake, client):
    fake.status = 502
    fake.status_body = b'{"error": {"code": 7}}'
    res = client(fake.url).evaluate("s", QUESTIONS)
    assert '{"code": 7}' in res["error"]


def test_http_json_non_dict_body_falls_back_to_raw(fake, client):
    fake.status = 502
    fake.status_body = b"[1, 2]"
    res = client(fake.url).evaluate("s", QUESTIONS)
    assert "[1, 2]" in res["error"]


def test_http_body_is_capped(fake, client):
    fake.status = 500
    fake.status_body = b"A" * 5000
    res = client(fake.url).evaluate("s", QUESTIONS)
    assert "A" * 500 in res["error"]
    assert "A" * 501 not in res["error"]


def test_http_body_invalid_utf8_does_not_raise(fake, client):
    fake.status = 500
    fake.status_body = b"\xff\xfe broken"
    res = client(fake.url).evaluate("s", QUESTIONS)
    assert res["error_kind"] == "http" and "broken" in res["error"]


# ---------- other error kinds ----------

def test_connection_refused_kind(client, closed_port_url):
    res = client(closed_port_url).evaluate("s", QUESTIONS)
    assert res["error_kind"] == "connection"
    assert res["error"].startswith("Failed to connect to Ollama at " + closed_port_url)
    assert "status" not in res


def test_timeout_kind_and_message(fake, client):
    fake.delay = 2
    res = client(fake.url).evaluate("s", QUESTIONS, timeout=0.3)
    assert res["error_kind"] == "timeout"
    assert "Timeout após 0.3s" in res["error"]
    assert fake.url in res["error"]
    assert "SYSTEMONE_TIMEOUT" in res["error"]
    assert "Failed to connect" not in res["error"]
    assert set(res) == {"error", "model", "error_kind"}


def test_invalid_json_body_kind(fake, client):
    fake.raw_body = b"<<not json>>"
    res = client(fake.url).evaluate("s", QUESTIONS)
    assert res["error_kind"] == "invalid_response"
    assert "Resposta inválida do Ollama (JSON malformado)" in res["error"]


def test_invalid_utf8_success_body_kind(fake, client):
    fake.raw_body = b"\xff\xfe"
    res = client(fake.url).evaluate("s", QUESTIONS)
    assert res["error_kind"] == "invalid_response"


def test_unexpected_error_kind(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr("systemone_gate.client.urllib.request.urlopen", boom)
    res = client("http://127.0.0.1:1/x").evaluate("s", QUESTIONS)
    assert res["error_kind"] == "unexpected"
    assert res["error"].startswith("Unexpected error during evaluation:") and "kaboom" in res["error"]


def test_success_result_has_no_error_keys(fake, client):
    fake.respond("nimble", {"model": "nimble", "answers": {"q": 1}, "usage": {}})
    res = client(fake.url).evaluate("s", QUESTIONS)
    assert res == {"model": "nimble", "answers": {"q": 1}, "usage": {}}


def test_evaluate_does_not_mutate_inputs(fake, client):
    questions = copy.deepcopy(QUESTIONS)
    client(fake.url).evaluate("s", questions)
    assert questions == QUESTIONS


# ---------- DEF-08: configurable timeout ----------

def _used_timeout(monkeypatch, c, **kwargs):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        raise RuntimeError("stop")
    monkeypatch.setattr("systemone_gate.client.urllib.request.urlopen", fake_urlopen)
    c.evaluate("s", QUESTIONS, **kwargs)
    return seen["timeout"]


def test_timeout_default_is_30(monkeypatch):
    monkeypatch.delenv("SYSTEMONE_TIMEOUT", raising=False)
    assert _used_timeout(monkeypatch, SystemOneClient(endpoint="http://127.0.0.1:9/")) == 30.0


def test_timeout_env_used(monkeypatch):
    monkeypatch.setenv("SYSTEMONE_TIMEOUT", "75.5")
    assert _used_timeout(monkeypatch, SystemOneClient(endpoint="http://127.0.0.1:9/")) == 75.5


def test_timeout_constructor_beats_env(monkeypatch):
    monkeypatch.setenv("SYSTEMONE_TIMEOUT", "75")
    c = SystemOneClient(endpoint="http://127.0.0.1:9/", timeout=12)
    assert _used_timeout(monkeypatch, c) == 12


def test_timeout_evaluate_arg_beats_constructor(monkeypatch):
    monkeypatch.setenv("SYSTEMONE_TIMEOUT", "75")
    c = SystemOneClient(endpoint="http://127.0.0.1:9/", timeout=12)
    assert _used_timeout(monkeypatch, c, timeout=5) == 5


def test_timeout_legacy_int_argument_still_works(fake, client):
    assert "answers" in client(fake.url).evaluate("s", QUESTIONS, timeout=5)


@pytest.mark.parametrize("value", ["abc", "0", "-1", "nan", "inf", "-inf", ""])
def test_invalid_env_timeout_raises_at_construction(monkeypatch, value):
    monkeypatch.setenv("SYSTEMONE_TIMEOUT", value)
    with pytest.raises(ValueError, match="SYSTEMONE_TIMEOUT"):
        SystemOneClient(endpoint="http://127.0.0.1:9/")


def test_invalid_env_ignored_when_constructor_timeout_given(monkeypatch):
    # constructor arg wins; env is not consulted at all
    monkeypatch.setenv("SYSTEMONE_TIMEOUT", "abc")
    assert SystemOneClient(endpoint="http://127.0.0.1:9/", timeout=3).timeout == 3


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_constructor_timeout_raises(value):
    with pytest.raises(ValueError, match="timeout"):
        SystemOneClient(endpoint="http://127.0.0.1:9/", timeout=value)


def test_cli_exits_2_on_invalid_timeout_env(fake, sub_env, repo_root):
    env = sub_env(fake.url)
    env["SYSTEMONE_TIMEOUT"] = "abc"
    out = subprocess.run([sys.executable, "-m", "systemone_gate.cli", "triage", "x"],
                         cwd=repo_root, env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 2
    assert "Configuração inválida" in out.stderr and "SYSTEMONE_TIMEOUT" in out.stderr
    assert fake.requests == []


def test_http_error_body_read_failure_does_not_raise(client, monkeypatch):
    import io
    import urllib.error

    class BrokenBody(io.BytesIO):
        def read(self, *a, **k):
            raise OSError("reset")

    def raise_http(*a, **k):
        raise urllib.error.HTTPError("http://127.0.0.1:9/", 503, "Service Unavailable", {}, BrokenBody())
    monkeypatch.setattr("systemone_gate.client.urllib.request.urlopen", raise_http)
    res = client("http://127.0.0.1:9/").evaluate("s", QUESTIONS)
    assert res["status"] == 503 and res["error_kind"] == "http"
