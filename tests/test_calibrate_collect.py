"""Hermetic tests for the calibration collection script (fake Ollama, no real model)."""

import importlib.util
import json
import os

import pytest

from systemone_gate.client import SystemOneClient
from systemone_gate.rubrics import RUBRIC_COMMAND_SAFETY, get_diff_rubric

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "benchmarks")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collect = _load("calibrate_collect", os.path.join(BENCH, "calibrate_collect.py"))

GUARD_ANSWERS = {
    "is_destructive": {"type": "choice", "choice": "safe", "probabilities": {"safe": 0.9}, "confidence": 0.7},
    "danger_score": {"type": "score", "score": 0.4, "probabilities": {"0": 0.7, "1": 0.2, "2": 0.1}, "confidence": 0.6},
}
OK_BODY = {"answers": GUARD_ANSWERS, "usage": {"input_tokens": 42, "output_tokens": 3}, "extra": "dropped"}

SAFE = {
    "id": "guard-fs-001",
    "state": "rm -rf node_modules",
    "should_block": False,
    "label_source": "synthetic",
    "label_evidence": "scoped removal of a regenerable dependency directory",
    "rules_catch": False,
    "review_status": "unreviewed",
    "second_label": None,
    "resolved_by": None,
}
CAUGHT = {**SAFE, "id": "guard-fs-002", "state": "rm -rf /", "should_block": True, "rules_catch": True}


def _client(fake):
    return SystemOneClient(endpoint=fake.url, timeout=5, redact=False)


def _write_dataset(tmp_path, cases, surface="guard", name="cases.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"schema_version": 1, "surface": surface, "cases": cases}), encoding="utf-8")
    return path


def test_state_hash_is_stable_and_short():
    assert collect.state_hash("ls") == collect.state_hash("ls")
    assert collect.state_hash("ls") != collect.state_hash("ls ")
    assert len(collect.state_hash("ls")) == 16


def test_rubric_for_matches_the_production_rubrics():
    assert collect.rubric_for("guard") == RUBRIC_COMMAND_SAFETY
    assert collect.rubric_for("diff") == get_diff_rubric("default")
    with pytest.raises(ValueError):
        collect.rubric_for("triage")


def test_collect_condition_records_compact_answers_and_integrity_fields(fake):
    fake.respond("nimble:latest", OK_BODY)
    results = collect.collect_condition(_client(fake), "nimble:latest", "guard", [SAFE])
    assert len(results) == 1
    row = results[0]
    assert row["id"] == "guard-fs-001"
    assert row["state_sha256_16"] == collect.state_hash("rm -rf node_modules")
    assert row["input_tokens"] == 42
    assert row["ms"] >= 0
    assert row["answers"]["is_destructive"]["choice"] == "safe"
    assert row["answers"]["danger_score"]["score"] == 0.4
    assert "extra" not in row


def test_collect_condition_sends_the_production_rubric_and_model(fake):
    fake.respond("tev1:0.8b", OK_BODY)
    collect.collect_condition(_client(fake), "tev1:0.8b", "guard", [SAFE])
    (request,) = fake.requests
    assert request["model"] == "tev1:0.8b"
    assert request["state"] == "rm -rf node_modules"
    assert request["questions"] == RUBRIC_COMMAND_SAFETY


def test_cases_caught_by_rules_still_reach_the_model(fake):
    fake.respond("nimble:latest", OK_BODY)
    collect.collect_condition(_client(fake), "nimble:latest", "guard", [CAUGHT])
    assert [r["state"] for r in fake.requests] == ["rm -rf /"]


def test_collect_condition_records_errors_without_aborting(fake):
    fake.status = 500
    results = collect.collect_condition(_client(fake), "nimble:latest", "guard", [SAFE, CAUGHT])
    assert [r["id"] for r in results] == ["guard-fs-001", "guard-fs-002"]
    assert all("error" in r and "answers" not in r for r in results)
    assert all(r["state_sha256_16"] for r in results)


def test_resume_reuses_ok_results_and_retries_errors(fake):
    fake.respond("nimble:latest", OK_BODY)
    client = _client(fake)
    first = collect.collect_condition(client, "nimble:latest", "guard", [SAFE])
    failed = {"id": "guard-fs-002", "state_sha256_16": collect.state_hash("rm -rf /"), "error": "boom", "ms": 1.0}
    fake_requests_before = len(fake.requests)
    results = collect.collect_condition(client, "nimble:latest", "guard", [SAFE, CAUGHT], previous=[first[0], failed])
    assert len(fake.requests) == fake_requests_before + 1  # only the failed case was re-sent
    assert fake.requests[-1]["state"] == "rm -rf /"
    assert results[0] == first[0]
    assert "answers" in results[1]


def test_resume_discards_results_whose_case_changed(fake):
    fake.respond("nimble:latest", OK_BODY)
    stale = {
        "id": "guard-fs-001",
        "state_sha256_16": collect.state_hash("old command"),
        "answers": collect.compact(GUARD_ANSWERS),
        "ms": 1.0,
    }
    results = collect.collect_condition(_client(fake), "nimble:latest", "guard", [SAFE], previous=[stale])
    assert len(fake.requests) == 1
    assert results[0]["state_sha256_16"] == collect.state_hash("rm -rf node_modules")


def test_collect_meta_is_http_only_and_records_model_digests(fake):
    fake.route("/api/version", {"version": "9.9.9"})
    fake.route("/api/tags", {"models": [{"name": "nimble:latest", "digest": "abc123"}]})
    meta = collect.collect_meta(_client(fake), ["nimble:latest", "tev1:0.8b"], {"a.json": "deadbeef"})
    assert meta["ollama_api_version"] == "9.9.9"
    assert meta["model_digests"] == {"nimble:latest": "abc123", "tev1:0.8b": None}
    assert meta["data_sha256_16"] == {"a.json": "deadbeef"}
    assert meta["models"] == ["nimble:latest", "tev1:0.8b"]
    assert "date" in meta and "git_head" in meta


def test_collect_meta_survives_an_unreachable_ollama():
    client = SystemOneClient(endpoint="http://127.0.0.1:1/v1/systemone", timeout=1, redact=False)
    meta = collect.collect_meta(client, ["nimble:latest"], {})
    assert meta["model_digests"] == {"nimble:latest": None}
    assert str(meta["ollama_api_version"]).startswith("unavailable")


# ---------------------------------------------------------------- main


def _digests(fake, **digests):
    fake.route("/api/tags", {"models": [{"name": n, "digest": d} for n, d in digests.items()]})


def _run(tmp_path, fake, *extra):
    data = tmp_path / "data"
    out = tmp_path / "out.json"
    if "/api/tags" not in fake.routes:
        _digests(fake, **{"nimble:latest": "d-nimble", "tev1:0.8b": "d-tev1"})
    argv = ["--data-dir", str(data), "--output", str(out), "--endpoint", fake.url, *extra]
    return collect.main(argv), out


def test_main_dry_run_validates_and_sends_nothing(tmp_path, fake, capsys):
    (tmp_path / "data").mkdir()
    _write_dataset(tmp_path / "data", [SAFE, CAUGHT])
    code, out = _run(tmp_path, fake, "--models", "nimble:latest", "tev1:0.8b", "--dry-run")
    assert code == 0
    assert fake.requests == []
    assert not out.exists()
    assert "4 requests" in capsys.readouterr().out


def test_main_rejects_an_invalid_dataset(tmp_path, fake, capsys):
    (tmp_path / "data").mkdir()
    _write_dataset(tmp_path / "data", [{**SAFE, "state": " "}])
    code, out = _run(tmp_path, fake, "--dry-run")
    assert code == 1
    assert "invalid" in capsys.readouterr().err


def test_main_full_run_writes_the_expected_document(tmp_path, fake):
    (tmp_path / "data").mkdir()
    _write_dataset(tmp_path / "data", [SAFE, CAUGHT])
    fake.respond("nimble:latest", OK_BODY)
    fake.respond("tev1:0.8b", OK_BODY)
    code, out = _run(tmp_path, fake, "--models", "nimble:latest", "tev1:0.8b")
    assert code == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1
    assert set(doc["runs"]) == {"guard|nimble:latest", "guard|tev1:0.8b"}
    assert [r["id"] for r in doc["runs"]["guard|nimble:latest"]] == ["guard-fs-001", "guard-fs-002"]
    assert doc["meta"]["models"] == ["nimble:latest", "tev1:0.8b"]
    assert len(fake.requests) == 4


def test_main_limit_uses_only_the_first_cases(tmp_path, fake):
    (tmp_path / "data").mkdir()
    _write_dataset(tmp_path / "data", [SAFE, CAUGHT])
    fake.respond("nimble:latest", OK_BODY)
    code, out = _run(tmp_path, fake, "--models", "nimble:latest", "--limit", "1")
    assert code == 0
    assert len(json.loads(out.read_text(encoding="utf-8"))["runs"]["guard|nimble:latest"]) == 1


def test_main_resume_only_resends_what_is_missing(tmp_path, fake):
    (tmp_path / "data").mkdir()
    _write_dataset(tmp_path / "data", [SAFE, CAUGHT])
    fake.respond("nimble:latest", OK_BODY)
    _run(tmp_path, fake, "--models", "nimble:latest", "--limit", "1")
    assert len(fake.requests) == 1
    code, out = _run(tmp_path, fake, "--models", "nimble:latest", "--resume")
    assert code == 0
    assert len(fake.requests) == 2  # only the second case
    rows = json.loads(out.read_text(encoding="utf-8"))["runs"]["guard|nimble:latest"]
    assert [r["id"] for r in rows] == ["guard-fs-001", "guard-fs-002"]
    assert all("answers" in r for r in rows)


def test_main_exit_code_is_one_when_requests_failed(tmp_path, fake):
    (tmp_path / "data").mkdir()
    _write_dataset(tmp_path / "data", [SAFE])
    fake.status = 500
    code, out = _run(tmp_path, fake, "--models", "nimble:latest")
    assert code == 1
    rows = json.loads(out.read_text(encoding="utf-8"))["runs"]["guard|nimble:latest"]
    assert "error" in rows[0]


def test_main_surface_without_cases_is_skipped(tmp_path, fake):
    (tmp_path / "data").mkdir()
    _write_dataset(tmp_path / "data", [SAFE])
    fake.respond("nimble:latest", OK_BODY)
    code, out = _run(tmp_path, fake, "--models", "nimble:latest", "--surfaces", "guard", "diff")
    assert code == 0
    assert set(json.loads(out.read_text(encoding="utf-8"))["runs"]) == {"guard|nimble:latest"}


# ---------------------------------------------------------------- review hardening


def _data(tmp_path, cases=None, surface="guard", name="cases.json"):
    (tmp_path / "data").mkdir(exist_ok=True)
    _write_dataset(tmp_path / "data", [SAFE, CAUGHT] if cases is None else cases, surface=surface, name=name)


def _saved(out):
    return json.loads(out.read_text(encoding="utf-8"))


def test_merge_rows_keeps_unselected_ids_and_replaces_selected_ones():
    old = [{"id": "a", "v": 1}, {"id": "b", "v": 1}]
    merged = collect.merge_rows(old, [{"id": "b", "v": 2}, {"id": "c", "v": 2}])
    assert merged == [{"id": "a", "v": 1}, {"id": "b", "v": 2}, {"id": "c", "v": 2}]
    assert collect.merge_rows(None, [{"id": "a"}]) == [{"id": "a"}]


def test_on_result_is_called_after_every_case_with_growing_rows(fake):
    fake.respond("nimble:latest", OK_BODY)
    seen = []
    collect.collect_condition(
        _client(fake), "nimble:latest", "guard", [SAFE, CAUGHT], on_result=lambda rows: seen.append(len(rows))
    )
    assert seen == [1, 2]


@pytest.mark.parametrize(
    "body",
    [{"foo": 1}, {"answers": {}}, {"answers": {"is_destructive": GUARD_ANSWERS["is_destructive"]}}, {"answers": []}],
)
def test_malformed_answers_become_a_failed_row_not_a_crash(fake, body):
    fake.respond("nimble:latest", body)
    (row,) = collect.collect_condition(_client(fake), "nimble:latest", "guard", [SAFE])
    assert row["error_kind"] == "invalid_response"
    assert "answers" not in row


def test_non_object_json_body_becomes_a_failed_row(fake):
    fake.raw_body = b"[1, 2]"
    (row,) = collect.collect_condition(_client(fake), "nimble:latest", "guard", [SAFE])
    assert row["error_kind"] == "invalid_response"


def test_empty_or_partial_answers_are_not_reusable(fake):
    fake.respond("nimble:latest", OK_BODY)
    previous = [{"id": "guard-fs-001", "state_sha256_16": collect.state_hash(SAFE["state"]), "answers": {}, "ms": 1.0}]
    collect.collect_condition(_client(fake), "nimble:latest", "guard", [SAFE], previous=previous)
    assert len(fake.requests) == 1


def test_missing_usage_is_tolerated(fake):
    fake.respond("nimble:latest", {"answers": GUARD_ANSWERS})
    (row,) = collect.collect_condition(_client(fake), "nimble:latest", "guard", [SAFE])
    assert row["input_tokens"] is None and "answers" in row


def test_diff_surface_uses_the_diff_rubric(fake):
    diff_case = {
        "id": "diff-syn-001",
        "state": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n",
        "should_block": False,
        "label_source": "synthetic",
        "label_evidence": "one-line constant change",
        "review_status": "unreviewed",
        "second_label": None,
        "resolved_by": None,
    }
    answers = {key: {"type": "choice", "probabilities": {}} for key in get_diff_rubric("default")}
    fake.respond("nimble:latest", {"answers": answers})
    (row,) = collect.collect_condition(_client(fake), "nimble:latest", "diff", [diff_case])
    assert "answers" in row
    assert fake.requests[0]["questions"] == get_diff_rubric("default")


def test_collect_meta_survives_http_protocol_errors(fake, monkeypatch):
    import http.client

    def boom(_url):
        raise http.client.BadStatusLine("garbage")

    monkeypatch.setattr(collect, "_http_json", boom)
    meta = collect.collect_meta(_client(fake), ["nimble:latest"], {})
    assert str(meta["ollama_api_version"]).startswith("unavailable")


def test_meta_records_rubric_hashes_timeout_and_package_state(fake):
    meta = collect.collect_meta(_client(fake), ["nimble:latest"], {}, ["guard"])
    assert meta["rubric_sha256_16"] == {"guard": collect.rubric_hash("guard")}
    assert meta["timeout_s"] == 5
    assert meta["git_dirty_package"] in (True, False, None)


def test_atomic_save_keeps_the_previous_file_when_writing_fails(tmp_path, monkeypatch):
    target = tmp_path / "out.json"
    collect._save(str(target), {"m": 1}, {"k": []})
    before = target.read_text(encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(collect.json, "dump", boom)
    with pytest.raises(OSError):
        collect._save(str(target), {"m": 2}, {"k": []})
    assert target.read_text(encoding="utf-8") == before
    assert [p.name for p in tmp_path.iterdir()] == ["out.json"]


def test_main_refuses_to_overwrite_without_a_flag(tmp_path, fake):
    _data(tmp_path)
    fake.respond("nimble:latest", OK_BODY)
    code, out = _run(tmp_path, fake, "--models", "nimble:latest")
    assert code == 0
    before = out.read_text(encoding="utf-8")
    requests = len(fake.requests)
    code, _ = _run(tmp_path, fake, "--models", "nimble:latest")
    assert code == 2
    assert len(fake.requests) == requests
    assert out.read_text(encoding="utf-8") == before
    code, _ = _run(tmp_path, fake, "--models", "nimble:latest", "--overwrite")
    assert code == 0
    assert len(fake.requests) == requests + 2


def test_resume_keeps_conditions_that_are_not_part_of_this_run(tmp_path, fake):
    _data(tmp_path)
    fake.respond("nimble:latest", OK_BODY)
    fake.respond("tev1:0.8b", OK_BODY)
    _run(tmp_path, fake, "--models", "nimble:latest", "tev1:0.8b")
    code, out = _run(tmp_path, fake, "--models", "nimble:latest", "--resume")
    assert code == 0
    assert set(_saved(out)["runs"]) == {"guard|nimble:latest", "guard|tev1:0.8b"}


def test_resume_with_limit_keeps_cases_outside_the_limit(tmp_path, fake):
    _data(tmp_path)
    fake.respond("nimble:latest", OK_BODY)
    _run(tmp_path, fake, "--models", "nimble:latest")
    requests = len(fake.requests)
    code, out = _run(tmp_path, fake, "--models", "nimble:latest", "--limit", "1", "--resume")
    assert code == 0
    assert len(fake.requests) == requests  # everything was already collected
    rows = _saved(out)["runs"]["guard|nimble:latest"]
    assert [r["id"] for r in rows] == ["guard-fs-001", "guard-fs-002"]


def test_resume_refuses_when_the_model_digest_changed(tmp_path, fake, capsys):
    _data(tmp_path)
    fake.respond("nimble:latest", OK_BODY)
    _run(tmp_path, fake, "--models", "nimble:latest")
    out = tmp_path / "out.json"
    before = out.read_text(encoding="utf-8")
    _digests(fake, **{"nimble:latest": "other-weights"})
    code, _ = _run(tmp_path, fake, "--models", "nimble:latest", "--resume")
    assert code == 2
    assert "digest" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == before


def test_resume_refuses_when_the_digest_cannot_be_verified(tmp_path, fake):
    _data(tmp_path)
    fake.respond("nimble:latest", OK_BODY)
    _digests(fake)  # server knows no models: digests are None
    _run(tmp_path, fake, "--models", "nimble:latest")
    code, _ = _run(tmp_path, fake, "--models", "nimble:latest", "--resume")
    assert code == 2


def test_resume_refuses_when_the_rubric_changed(tmp_path, fake, capsys):
    _data(tmp_path)
    fake.respond("nimble:latest", OK_BODY)
    _, out = _run(tmp_path, fake, "--models", "nimble:latest")
    doc = _saved(out)
    doc["meta"]["rubric_sha256_16"]["guard"] = "0" * 16
    out.write_text(json.dumps(doc), encoding="utf-8")
    code, _ = _run(tmp_path, fake, "--models", "nimble:latest", "--resume")
    assert code == 2
    assert "rubrica" in capsys.readouterr().err


@pytest.mark.parametrize(
    "content", ["{truncated", '{"schema_version": 99, "meta": {}, "runs": {}}', '{"schema_version": 1}']
)
def test_resume_refuses_a_corrupt_output_and_leaves_it_untouched(tmp_path, fake, content):
    _data(tmp_path)
    out = tmp_path / "out.json"
    out.write_text(content, encoding="utf-8")
    code, _ = _run(tmp_path, fake, "--models", "nimble:latest", "--resume")
    assert code == 2
    assert out.read_text(encoding="utf-8") == content
    assert fake.requests == []


def test_resume_with_no_saved_output_starts_a_new_one(tmp_path, fake):
    _data(tmp_path)
    fake.respond("nimble:latest", OK_BODY)
    code, out = _run(tmp_path, fake, "--models", "nimble:latest", "--resume")
    assert code == 0 and out.exists()


@pytest.mark.parametrize("value", ["0", "-1", "x"])
def test_limit_must_be_a_positive_integer(tmp_path, fake, value):
    _data(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, fake, "--limit", value)
    assert exc.value.code == 2


def test_no_cases_in_the_selected_surfaces_is_an_error_and_writes_nothing(tmp_path, fake):
    _data(tmp_path)
    code, out = _run(tmp_path, fake, "--surfaces", "diff")
    assert code == 2
    assert not out.exists() and fake.requests == []


def test_duplicate_models_are_collected_once(tmp_path, fake):
    _data(tmp_path)
    fake.respond("nimble:latest", OK_BODY)
    code, out = _run(tmp_path, fake, "--models", "nimble:latest", "nimble:latest")
    assert code == 0
    assert len(fake.requests) == 2
    assert list(_saved(out)["runs"]) == ["guard|nimble:latest"]


def test_main_malformed_response_exits_one_without_a_traceback(tmp_path, fake):
    _data(tmp_path, [SAFE])
    fake.respond("nimble:latest", {"foo": 1})
    code, out = _run(tmp_path, fake, "--models", "nimble:latest")
    assert code == 1
    assert _saved(out)["runs"]["guard|nimble:latest"][0]["error_kind"] == "invalid_response"


def test_main_progress_survives_an_interrupt(tmp_path, fake, monkeypatch):
    _data(tmp_path)
    fake.respond("nimble:latest", OK_BODY)
    real = collect.SystemOneClient.evaluate
    calls = {"n": 0}

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real(self, *args, **kwargs)

    monkeypatch.setattr(collect.SystemOneClient, "evaluate", flaky)
    code, out = _run(tmp_path, fake, "--models", "nimble:latest")
    assert code == 130
    assert [r["id"] for r in _saved(out)["runs"]["guard|nimble:latest"]] == ["guard-fs-001"]
    monkeypatch.undo()
    code, out = _run(tmp_path, fake, "--models", "nimble:latest", "--resume")
    assert code == 0
    assert len(_saved(out)["runs"]["guard|nimble:latest"]) == 2
