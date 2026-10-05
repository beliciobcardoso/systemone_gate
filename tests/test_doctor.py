import pytest

from systemone_gate.cli import main
from systemone_gate.doctor import (
    MIN_OLLAMA_VERSION,
    OK,
    WARN,
    FAIL,
    derive_base_url,
    parse_version,
    run_doctor,
)

SHORT = 0.5


def _tag(name, caps=("completion", "decision")):
    entry = {"name": name, "model": name}
    if caps is not None:
        entry["capabilities"] = list(caps)
    return entry


def _healthy(fake, version="0.35.1", tags=None):
    fake.route("/api/version", {"version": version})
    fake.route("/api/tags", {"models": tags if tags is not None else [_tag("tev1:0.8b"), _tag("nimble:latest")]})
    return fake


def _smoke_ok(fake):
    fake.respond("tev1:0.8b", {"answers": {"ping": {"type": "choice", "choice": "yes",
                                                     "probabilities": {"yes": 0.9, "no": 0.1}}}})


def _statuses(report):
    return [c.status for c in report.checks]


def _by_name(report, name):
    return next(c for c in report.checks if c.name == name)


def test_all_good(fake):
    _smoke_ok(_healthy(fake))
    report = run_doctor(fake.url, timeout=SHORT)
    assert report.ok
    assert _statuses(report) == [OK, OK, OK, OK]
    assert [c.name for c in report.checks] == ["reachability", "version", "models", "smoke"]
    assert fake.requests[0]["model"] == "tev1:0.8b"


def test_checks_have_message_and_hint_fields(fake):
    _smoke_ok(_healthy(fake))
    for check in run_doctor(fake.url, timeout=SHORT).checks:
        assert check.message
        assert isinstance(check.hint, str)


def test_version_too_old_fails_with_upgrade_hint(fake):
    _healthy(fake, version="0.34.9")
    report = run_doctor(fake.url, timeout=SHORT)
    check = _by_name(report, "version")
    assert check.status == FAIL
    assert MIN_OLLAMA_VERSION in check.message
    assert "Atualize" in check.hint
    assert not report.ok


def test_version_with_suffix_is_accepted(fake):
    _smoke_ok(_healthy(fake, version="0.35.1-rc1"))
    assert _by_name(run_doctor(fake.url, timeout=SHORT), "version").status == OK


def test_version_exactly_minimum_is_ok(fake):
    _healthy(fake, version=MIN_OLLAMA_VERSION)
    assert _by_name(run_doctor(fake.url, smoke=False, timeout=SHORT), "version").status == OK


def test_unparsable_version_warns(fake):
    _smoke_ok(_healthy(fake, version="banana"))
    report = run_doctor(fake.url, timeout=SHORT)
    assert _by_name(report, "version").status == WARN
    assert report.ok


def test_missing_version_field_warns(fake):
    _healthy(fake)
    fake.route("/api/version", {"other": 1})
    assert _by_name(run_doctor(fake.url, smoke=False, timeout=SHORT), "version").status == WARN


def test_model_missing_fails_with_pull_hint(fake):
    _healthy(fake, tags=[_tag("tev1:0.8b")])
    report = run_doctor(fake.url, timeout=SHORT)
    check = _by_name(report, "models")
    assert check.status == FAIL
    assert "nimble" in check.message
    assert "ollama pull nimble" in check.hint
    assert not report.ok


def test_model_without_decision_capability_warns(fake):
    _smoke_ok(_healthy(fake, tags=[_tag("tev1:0.8b", caps=("completion",)), _tag("nimble:latest")]))
    report = run_doctor(fake.url, timeout=SHORT)
    check = _by_name(report, "models")
    assert check.status == WARN
    assert "tev1:0.8b" in check.message
    assert report.ok


def test_model_without_capabilities_field_warns(fake):
    _healthy(fake, tags=[_tag("tev1:0.8b", caps=None), _tag("nimble:latest")])
    assert _by_name(run_doctor(fake.url, smoke=False, timeout=SHORT), "models").status == WARN


def test_missing_beats_capability_warning(fake):
    _healthy(fake, tags=[_tag("tev1:0.8b", caps=("completion",))])
    assert _by_name(run_doctor(fake.url, smoke=False, timeout=SHORT), "models").status == FAIL


def test_tag_matching_is_tolerant_both_ways(fake):
    _healthy(fake, tags=[_tag("tev1:0.8b"), _tag("nimble:latest")])
    report = run_doctor(fake.url, models=("nimble", "nimble:latest"), smoke=False, timeout=SHORT)
    assert _by_name(report, "models").status == OK
    _healthy(fake, tags=[_tag("nimble")])
    report = run_doctor(fake.url, models=("nimble:latest",), smoke=False, timeout=SHORT)
    assert _by_name(report, "models").status == OK


def test_custom_models_override_defaults(fake):
    _healthy(fake, tags=[_tag("custom:1b")])
    fake.respond("custom:1b", {"answers": {"ping": {"choice": "yes", "probabilities": {"yes": 1.0}}}})
    report = run_doctor(fake.url, models=("custom:1b",), timeout=SHORT)
    assert report.ok
    assert fake.requests[0]["model"] == "custom:1b"


def test_tags_with_garbage_shape_fails(fake):
    _healthy(fake)
    fake.route("/api/tags", {"models": "nope"})
    assert _by_name(run_doctor(fake.url, smoke=False, timeout=SHORT), "models").status == FAIL


def test_tags_http_error_fails(fake):
    _healthy(fake)
    fake.route("/api/tags", b'{"error": "boom"}', status=500)
    check = _by_name(run_doctor(fake.url, smoke=False, timeout=SHORT), "models")
    assert check.status == FAIL
    assert "500" in check.message


def test_version_404_fails_and_stops(fake):
    report = run_doctor(fake.url, timeout=SHORT)
    assert [c.name for c in report.checks] == ["reachability"]
    assert report.checks[0].status == FAIL
    assert "404" in report.checks[0].message
    assert "0.35" in report.checks[0].hint
    assert fake.requests == []


def test_connection_refused(closed_port_url):
    report = run_doctor(closed_port_url, timeout=SHORT)
    assert [c.name for c in report.checks] == ["reachability"]
    check = report.checks[0]
    assert check.status == FAIL
    assert "conectar" in check.message.lower()
    assert "ollama serve" in check.hint


def test_timeout(fake):
    _healthy(fake)
    fake.delay = 1.0
    report = run_doctor(fake.url, timeout=0.2)
    assert report.checks[0].status == FAIL
    assert "timeout" in report.checks[0].message.lower()


def test_invalid_json(fake):
    fake.route("/api/version", b"<html>nope")
    report = run_doctor(fake.url, timeout=SHORT)
    assert report.checks[0].status == FAIL
    assert "JSON" in report.checks[0].message


def test_non_object_json_is_invalid(fake):
    fake.route("/api/version", b"[1]")
    assert run_doctor(fake.url, timeout=SHORT).checks[0].status == FAIL


@pytest.mark.parametrize("answers", [
    None,
    "text",
    {},
    {"ping": "x"},
    {"ping": {"probabilities": {"yes": 1.0}}},
    {"ping": {"choice": 3, "probabilities": {"yes": 1.0}}},
    {"ping": {"choice": "yes"}},
    {"ping": {"choice": "yes", "probabilities": [1.0]}},
])
def test_smoke_shape_drift_fails(fake, answers):
    _healthy(fake)
    body = {"model": "tev1:0.8b"}
    if answers is not None:
        body["answers"] = answers
    fake.respond("tev1:0.8b", body)
    report = run_doctor(fake.url, timeout=SHORT)
    smoke = _by_name(report, "smoke")
    assert smoke.status == FAIL
    assert smoke.message
    assert not report.ok


def test_smoke_missing_answers_names_field(fake):
    _healthy(fake)
    fake.respond("tev1:0.8b", {"model": "x"})
    assert "answers" in _by_name(run_doctor(fake.url, timeout=SHORT), "smoke").message


def test_smoke_backend_error_fails(fake):
    _healthy(fake)
    fake.status = 500
    smoke = _by_name(run_doctor(fake.url, timeout=SHORT), "smoke")
    assert smoke.status == FAIL
    assert "500" in smoke.message


def test_no_smoke_skips_post(fake):
    _healthy(fake)
    report = run_doctor(fake.url, smoke=False, timeout=SHORT)
    assert [c.name for c in report.checks] == ["reachability", "version", "models"]
    assert fake.requests == []


def test_smoke_not_run_when_earlier_check_fails(fake):
    _healthy(fake, tags=[])
    report = run_doctor(fake.url, timeout=SHORT)
    assert "smoke" not in [c.name for c in report.checks]
    assert fake.requests == []


def test_smoke_runs_after_warnings(fake):
    _smoke_ok(_healthy(fake, version="weird"))
    assert "smoke" in [c.name for c in run_doctor(fake.url, timeout=SHORT).checks]


def test_report_is_frozen(fake):
    _healthy(fake)
    report = run_doctor(fake.url, smoke=False, timeout=SHORT)
    with pytest.raises(Exception):
        report.checks = ()
    with pytest.raises(Exception):
        report.checks[0].status = FAIL


@pytest.mark.parametrize("endpoint,expected", [
    ("http://localhost:11434/v1/systemone", "http://localhost:11434"),
    ("http://localhost:11434", "http://localhost:11434"),
    ("http://localhost:11434/", "http://localhost:11434"),
    ("https://ollama.example.com/v1/systemone", "https://ollama.example.com"),
    ("http://10.0.0.5/some/deep/path?x=1", "http://10.0.0.5"),
    ("http://user:secret@host:1234/v1/systemone", "http://host:1234"),
])
def test_derive_base_url(endpoint, expected):
    assert derive_base_url(endpoint) == expected


@pytest.mark.parametrize("endpoint", ["", "localhost:11434", "ftp://x/y", "not a url"])
def test_derive_base_url_invalid_raises_value_error(endpoint):
    with pytest.raises(ValueError):
        derive_base_url(endpoint)


@pytest.mark.parametrize("raw,expected", [
    ("0.35.0", (0, 35, 0)),
    ("0.35.1-rc1", (0, 35, 1)),
    ("v0.36", (0, 36, 0)),
    ("1.2.3+build", (1, 2, 3)),
    ("banana", None),
    ("", None),
    (None, None),
    (35, None),
])
def test_parse_version(raw, expected):
    assert parse_version(raw) == expected


def test_secrets_in_endpoint_are_not_printed(fake, capsys, monkeypatch):
    _smoke_ok(_healthy(fake))
    monkeypatch.setenv("OLLAMA_SYSTEMONE_URL", fake.url)
    # DEFAULT_ENDPOINT is import-time; the CLI path reads client.endpoint
    report = run_doctor(fake.url.replace("//", "//user:secret@"), timeout=SHORT)
    text = " ".join(c.message + c.hint for c in report.checks)
    assert "secret" not in text


# ---- CLI ----

@pytest.fixture
def cli_endpoint(monkeypatch):
    import systemone_gate.cli as cli
    from systemone_gate.client import SystemOneClient

    def use(url):
        monkeypatch.setattr(cli, "SystemOneClient", lambda: SystemOneClient(endpoint=url))
    return use


def test_cli_exit_0_and_checklist(fake, cli_endpoint, capsys):
    _smoke_ok(_healthy(fake))
    cli_endpoint(fake.url)
    with pytest.raises(SystemExit) as e:
        main(["doctor"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert out.count("\n") >= 5
    assert "OK" in out or "✅" in out


def test_cli_exit_1_when_fail(fake, cli_endpoint, capsys):
    _healthy(fake, version="0.1.0")
    cli_endpoint(fake.url)
    with pytest.raises(SystemExit) as e:
        main(["doctor", "--no-smoke"])
    assert e.value.code == 1
    assert "0.35.0" in capsys.readouterr().out


def test_cli_exit_0_with_only_warnings(fake, cli_endpoint):
    _healthy(fake, version="weird")
    cli_endpoint(fake.url)
    with pytest.raises(SystemExit) as e:
        main(["doctor", "--no-smoke"])
    assert e.value.code == 0


def test_cli_model_repeatable(fake, cli_endpoint, capsys):
    _healthy(fake, tags=[_tag("a:1"), _tag("b:2")])
    cli_endpoint(fake.url)
    with pytest.raises(SystemExit) as e:
        main(["doctor", "--no-smoke", "--model", "a:1", "--model", "b:2"])
    assert e.value.code == 0
    with pytest.raises(SystemExit) as e:
        main(["doctor", "--no-smoke", "--model", "a:1", "--model", "zzz"])
    assert e.value.code == 1
    assert "ollama pull zzz" in capsys.readouterr().out


def test_cli_no_smoke_sends_no_post(fake, cli_endpoint):
    _healthy(fake)
    cli_endpoint(fake.url)
    with pytest.raises(SystemExit):
        main(["doctor", "--no-smoke"])
    assert fake.requests == []


def test_cli_connection_refused_exit_1(closed_port_url, cli_endpoint, capsys):
    cli_endpoint(closed_port_url)
    with pytest.raises(SystemExit) as e:
        main(["doctor"])
    assert e.value.code == 1
    assert "Traceback" not in capsys.readouterr().err


def test_cli_invalid_timeout_exits_2(monkeypatch, capsys):
    monkeypatch.setenv("SYSTEMONE_TIMEOUT", "abc")
    with pytest.raises(SystemExit) as e:
        main(["doctor"])
    assert e.value.code == 2


def test_cli_invalid_endpoint_exits_2(cli_endpoint, capsys):
    cli_endpoint("not a url")
    with pytest.raises(SystemExit) as e:
        main(["doctor"])
    assert e.value.code == 2
    assert capsys.readouterr().err
