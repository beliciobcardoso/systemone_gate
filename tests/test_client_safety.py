import subprocess
import sys

import pytest

from systemone_gate import client as client_module
from systemone_gate.client import SystemOneClient

QUESTIONS = {"q": {"type": "choice", "instructions": "x", "criteria": {"a": None, "b": None}}}
SECRET = "hunter2" + "hunter2"  # assembled: keeps scanners quiet
LEAKY = "DB_PASSWORD=" + SECRET


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("SYSTEMONE_ALLOW_REMOTE", "SYSTEMONE_REDACT", "OLLAMA_SYSTEMONE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(client_module, "_remote_warned", False)


@pytest.mark.parametrize("url", [
    "http://localhost:11434/v1/systemone",
    "https://localhost/v1/systemone",
    "http://LOCALHOST:11434",
    "http://127.0.0.1:11434/v1/systemone",
    "http://127.1.2.3:8080/x",
    "http://[::1]:11434/v1/systemone",
    "http://ollama.localhost:11434/",
    "http://user:pw@localhost:11434/",
    "http://127.0.0.1:9/",
])
def test_loopback_http_endpoints_are_accepted(url, capsys):
    assert SystemOneClient(endpoint=url).endpoint == url
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("url,fragment", [
    ("file:///etc/passwd", "scheme"),
    ("ftp://localhost/x", "scheme"),
    ("", "scheme"),
    ("localhost:11434/v1/systemone", "scheme"),
    ("//localhost/x", "scheme"),
    ("http:///x", "host"),
    ("http://10.0.0.5:11434/", "remote host"),
    ("http://192.168.1.10/", "remote host"),
    ("https://api.example.com/v1/systemone", "remote host"),
    ("http://localhost.evil.com/", "remote host"),
    ("http://127.0.0.1.evil.com/", "remote host"),
    ("http://[2001:db8::1]/", "remote host"),
    ("http://0.0.0.0:11434/", "remote host"),
    ("http://localhost:notaport/", "Invalid endpoint"),
    ("http://[::1/", "Invalid endpoint"),
])
def test_invalid_or_remote_endpoints_are_rejected(url, fragment):
    with pytest.raises(ValueError) as exc:
        SystemOneClient(endpoint=url)
    assert fragment in str(exc.value)


def test_remote_error_mentions_allow_variable():
    with pytest.raises(ValueError, match="SYSTEMONE_ALLOW_REMOTE=1 to allow it"):
        SystemOneClient(endpoint="http://example.com/")


def test_userinfo_is_never_echoed_in_errors():
    for url in ("ftp://admin:" + SECRET + "@example.com/", "http://admin:" + SECRET + "@example.com/"):
        with pytest.raises(ValueError) as exc:
            SystemOneClient(endpoint=url)
        assert SECRET not in str(exc.value)
        assert "admin" not in str(exc.value)


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_allow_remote_env_permits_and_warns_once(monkeypatch, capsys, value):
    monkeypatch.setenv("SYSTEMONE_ALLOW_REMOTE", value)
    SystemOneClient(endpoint="https://api.example.com/v1/systemone")
    SystemOneClient(endpoint="http://10.0.0.5:11434/")
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "will leave this machine" in err


def test_warning_does_not_leak_userinfo(monkeypatch, capsys):
    monkeypatch.setenv("SYSTEMONE_ALLOW_REMOTE", "1")
    SystemOneClient(endpoint="https://u:" + SECRET + "@example.com/")
    assert SECRET not in capsys.readouterr().err


@pytest.mark.parametrize("value", ["", "0"])
def test_allow_remote_empty_or_zero_does_not_permit(monkeypatch, value):
    monkeypatch.setenv("SYSTEMONE_ALLOW_REMOTE", value)
    with pytest.raises(ValueError):
        SystemOneClient(endpoint="http://example.com/")


def test_allow_remote_does_not_permit_bad_scheme(monkeypatch):
    monkeypatch.setenv("SYSTEMONE_ALLOW_REMOTE", "1")
    with pytest.raises(ValueError, match="scheme"):
        SystemOneClient(endpoint="file:///etc/passwd")


def _construct_in_subprocess(repo_root, env_extra):
    import os
    env = dict(os.environ, PYTHONPATH=repo_root)
    for name in ("OLLAMA_SYSTEMONE_URL", "SYSTEMONE_ALLOW_REMOTE"):
        env.pop(name, None)
    env.update(env_extra)
    code = "from systemone_gate.client import SystemOneClient as C; print(C().endpoint)"
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30)


def test_env_url_is_validated(repo_root):
    bad = _construct_in_subprocess(repo_root, {"OLLAMA_SYSTEMONE_URL": "file:///etc/passwd"})
    assert bad.returncode != 0 and "scheme" in bad.stderr
    remote = _construct_in_subprocess(repo_root, {"OLLAMA_SYSTEMONE_URL": "http://example.com/x"})
    assert remote.returncode != 0 and "remote host" in remote.stderr
    ok = _construct_in_subprocess(repo_root, {"OLLAMA_SYSTEMONE_URL": "http://127.0.0.1:1/x"})
    assert ok.returncode == 0 and ok.stdout.strip() == "http://127.0.0.1:1/x"


def test_cli_reports_invalid_endpoint_as_config_error(repo_root):
    import os
    env = dict(os.environ, PYTHONPATH=repo_root, OLLAMA_SYSTEMONE_URL="file:///etc/passwd")
    env.pop("SYSTEMONE_ALLOW_REMOTE", None)
    res = subprocess.run([sys.executable, "-m", "systemone_gate.cli", "triage", "boom"],
                         env=env, capture_output=True, text=True, timeout=30)
    assert res.returncode == 2
    assert "Invalid configuration" in res.stderr


# ---- redaction wiring -------------------------------------------------------

def test_state_is_redacted_before_sending_and_count_reported(fake, client):
    res = client(fake.url).evaluate("+" + LEAKY + "\n", QUESTIONS)
    sent = fake.requests[0]
    assert SECRET not in sent["state"]
    assert sent["state"] == "+DB_PASSWORD=[REDACTED:secret_assignment]\n"
    assert sent["questions"] == QUESTIONS
    assert res["redacted"] == 1
    assert SECRET not in str(res)


def test_clean_state_has_no_redacted_key(fake, client):
    res = client(fake.url).evaluate("plain text", QUESTIONS)
    assert "redacted" not in res
    assert fake.requests[0]["state"] == "plain text"


def test_error_results_are_unchanged(closed_port_url, client):
    res = client(closed_port_url).evaluate(LEAKY, QUESTIONS)
    assert res["error_kind"] == "connection"
    assert "redacted" not in res


@pytest.mark.parametrize("method", ["triage_error", "review_diff", "route_task", "guard_command"])
def test_all_entry_points_redact(fake, client, method):
    getattr(client(fake.url), method)("echo " + LEAKY)
    assert SECRET not in fake.requests[0]["state"]


def test_redaction_can_be_disabled_by_argument(fake, client):
    res = client(fake.url, redact=False).evaluate(LEAKY, QUESTIONS)
    assert fake.requests[0]["state"] == LEAKY
    assert "redacted" not in res


def test_redaction_can_be_disabled_by_env(fake, client, monkeypatch):
    monkeypatch.setenv("SYSTEMONE_REDACT", "0")
    client(fake.url).evaluate(LEAKY, QUESTIONS)
    assert fake.requests[0]["state"] == LEAKY


def test_env_other_values_keep_redaction_on(fake, client, monkeypatch):
    monkeypatch.setenv("SYSTEMONE_REDACT", "1")
    client(fake.url).evaluate(LEAKY, QUESTIONS)
    assert SECRET not in fake.requests[0]["state"]


def test_argument_overrides_env(fake, client, monkeypatch):
    monkeypatch.setenv("SYSTEMONE_REDACT", "0")
    client(fake.url, redact=True).evaluate(LEAKY, QUESTIONS)
    assert SECRET not in fake.requests[0]["state"]
    monkeypatch.setenv("SYSTEMONE_REDACT", "1")
    client(fake.url, redact=False).evaluate(LEAKY, QUESTIONS)
    assert fake.requests[1]["state"] == LEAKY


def test_non_str_state_is_passed_through(fake, client):
    client(fake.url).evaluate(None, QUESTIONS)  # type: ignore[arg-type]
    assert fake.requests[0]["state"] is None


def test_guard_rules_see_the_raw_command(closed_port_url, client):
    res = client(closed_port_url).guard_command("rm -rf / # " + LEAKY)
    assert res["source"] == "rules"
    assert res["rule"]["id"]


def test_questions_are_not_mutated(fake, client):
    import copy
    questions = copy.deepcopy(QUESTIONS)
    client(fake.url).evaluate(LEAKY, questions)
    assert questions == QUESTIONS
