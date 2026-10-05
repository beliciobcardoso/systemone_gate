import json
import subprocess
import sys

import pytest

from systemone_gate import rubrics

QUESTIONS = {"q": {"type": "choice", "instructions": "x", "criteria": {"a": None, "b": None}}}


def test_evaluate_payload_shape_and_passthrough(fake, client):
    fake.respond("nimble", {"model": "nimble", "answers": {"q": {"choice": "a"}}, "usage": {"t": 3}})
    res = client(fake.url).evaluate("some state", QUESTIONS)
    assert res == {"model": "nimble", "answers": {"q": {"choice": "a"}}, "usage": {"t": 3}}
    assert fake.requests == [{"model": "nimble", "state": "some state", "questions": QUESTIONS}]


def test_evaluate_explicit_model_overrides_default(fake, client):
    client(fake.url).evaluate("s", QUESTIONS, model="custom:1b")
    assert fake.requests[0]["model"] == "custom:1b"


def test_client_custom_default_models(fake, client):
    c = client(fake.url, default_model="dm", fast_model="fm")
    c.triage_error("e")
    c.guard_command("ls -la")
    assert [r["model"] for r in fake.requests] == ["dm", "fm"]


@pytest.mark.parametrize("method,arg,rubric,model", [
    ("triage_error", "boom: segfault", rubrics.RUBRIC_ERROR_TRIAGE, "nimble"),
    ("review_diff", "diff --git a b", rubrics.RUBRIC_DIFF_RISK, "nimble"),
    ("route_task", "refactor auth", rubrics.RUBRIC_AGENT_ROUTING, "nimble"),
    ("guard_command", "ls -la", rubrics.RUBRIC_COMMAND_SAFETY, "tev1:0.8b"),
])
def test_method_default_model_and_forwarding(fake, client, method, arg, rubric, model):
    getattr(client(fake.url), method)(arg)
    assert fake.requests == [{"model": model, "state": arg, "questions": rubric}]


@pytest.mark.parametrize("method,arg", [
    ("triage_error", "e"), ("review_diff", "d"), ("route_task", "p"), ("guard_command", "git status"),
])
def test_method_explicit_model_override(fake, client, method, arg):
    getattr(client(fake.url), method)(arg, model="other:9b")
    assert fake.requests[0]["model"] == "other:9b"


def test_guard_default_is_fast_model_triage_is_nimble(fake, client):
    c = client(fake.url)
    c.guard_command("git status")
    c.triage_error("x")
    assert [r["model"] for r in fake.requests] == ["tev1:0.8b", "nimble"]


def test_connection_refused_returns_error_dict(client, closed_port_url):
    res = client(closed_port_url).evaluate("s", QUESTIONS)
    assert set(res) == {"error", "model"}
    assert res["model"] == "nimble"
    assert closed_port_url in res["error"]


def test_timeout_returns_error_dict(fake, client):
    fake.delay = 3
    res = client(fake.url).evaluate("s", QUESTIONS, timeout=1)
    assert "error" in res and res["model"] == "nimble"


def test_invalid_json_body_returns_error_dict(fake, client):
    fake.raw_body = b"<<not json>>"
    res = client(fake.url).evaluate("s", QUESTIONS, model="m")
    assert "error" in res and res["model"] == "m"


def test_http_500_returns_error_dict(fake, client):
    fake.status = 500
    res = client(fake.url).evaluate("s", QUESTIONS)
    assert "error" in res and "answers" not in res


def test_schema_divergent_response_is_passed_through(fake, client):
    fake.divergent = True
    res = client(fake.url).evaluate("s", QUESTIONS)
    assert "answers" not in res and "error" not in res  # callers must .get("answers")


@pytest.mark.xfail(strict=True, reason="DEF-06: HTTPError (e.g. 404) is reported as 'Failed to connect'")
def test_http_404_error_identifies_http_problem(fake, client):
    fake.status = 404
    res = client(fake.url).evaluate("s", QUESTIONS)
    assert "404" in res["error"]
    assert "Failed to connect" not in res["error"]  # an HTTP answer is not a connection failure


def test_env_var_sets_default_endpoint():
    code = (
        "import json\n"
        "from systemone_gate import client\n"
        "print(json.dumps(client.DEFAULT_ENDPOINT))\n"
    )
    import os
    from conftest import REPO_ROOT
    env = dict(os.environ, OLLAMA_SYSTEMONE_URL="http://example.invalid:1/x", PYTHONPATH=REPO_ROOT)
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == "http://example.invalid:1/x"


def test_env_var_endpoint_used_end_to_end(fake, sub_env, repo_root):
    code = (
        "import json\n"
        "from systemone_gate.client import SystemOneClient\n"
        "print(json.dumps(SystemOneClient().triage_error('x')))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=repo_root, env=sub_env(fake.url),
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert "answers" in json.loads(out.stdout)
    assert len(fake.requests) == 1
