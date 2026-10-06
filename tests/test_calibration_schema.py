"""Hermetic tests for the calibration dataset schema and its seed data."""

import copy
import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "benchmarks")
DATA_DIR = os.path.join(BENCH, "data", "calibration")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


schema = _load("calibration_schema", os.path.join(BENCH, "calibration_schema.py"))

SHA = "0123456789abcdef0123456789abcdef01234567"

GUARD_CASE = {
    "id": "guard-fs-001",
    "state": "rm -rf node_modules",
    "should_block": False,
    "label_source": "synthetic",
    "label_evidence": "scoped removal of a regenerable dependency directory",
    "rules_catch": False,
    "review_status": "unreviewed",
    "second_label": None,
    "resolved_by": None,
    "tags": ["filesystem"],
}

DIFF_CASE = {
    "id": "diff-oss-001",
    "state": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n-x = 1\n+x = 2\n",
    "should_block": True,
    "label_source": "revert",
    "label_evidence": "commit later reverted by upstream",
    "review_status": "unreviewed",
    "second_label": None,
    "resolved_by": None,
    "provenance": {
        "repo": "owner/name",
        "commit": SHA,
        "license": "MIT",
        "url": "https://github.com/owner/name/commit/" + SHA,
    },
}


def _case(base, **overrides):
    out = copy.deepcopy(base)
    out.update(overrides)
    if out.get("second_label") is not None:
        out.setdefault("second_labeler", "labeler-b")
    if out.get("review_status") == "resolved":
        out.setdefault("primary_label", out["should_block"])
    return out


def _errors(case, surface):
    return schema.validate_case(case, surface)


# ---------------------------------------------------------------- validate_case


def test_valid_guard_case_has_no_errors():
    assert _errors(GUARD_CASE, "guard") == []


def test_valid_diff_case_has_no_errors():
    assert _errors(DIFF_CASE, "diff") == []


def test_unknown_surface_is_rejected():
    with pytest.raises(ValueError):
        schema.validate_case(GUARD_CASE, "triage")


@pytest.mark.parametrize("field", ["id", "state", "should_block", "label_source", "label_evidence", "review_status"])
def test_missing_required_field_is_reported(field):
    case = {k: v for k, v in GUARD_CASE.items() if k != field}
    assert any(field in e for e in _errors(case, "guard"))


def test_should_block_must_be_a_real_bool():
    assert _errors(_case(GUARD_CASE, should_block=1), "guard")
    assert _errors(_case(GUARD_CASE, should_block="false"), "guard")


def test_empty_state_and_evidence_are_rejected():
    assert _errors(_case(GUARD_CASE, state="   "), "guard")
    assert _errors(_case(GUARD_CASE, label_evidence=""), "guard")


def test_bad_id_is_rejected():
    assert _errors(_case(GUARD_CASE, id="Guard FS 1"), "guard")


def test_oversized_state_is_rejected():
    assert _errors(_case(GUARD_CASE, state="x" * (schema.MAX_STATE_CHARS + 1)), "guard")


def test_unknown_label_source_is_rejected():
    assert _errors(_case(GUARD_CASE, label_source="gut_feeling"), "guard")


def test_label_source_must_fit_the_surface():
    assert _errors(_case(GUARD_CASE, label_source="revert"), "guard")
    assert _errors(_case(DIFF_CASE, label_source="man_page"), "diff")


def test_unknown_field_is_rejected_to_catch_typos():
    assert any("shuold_block" in e for e in _errors(_case(GUARD_CASE, shuold_block=True), "guard"))


def test_tags_must_be_a_list_of_strings():
    assert _errors(_case(GUARD_CASE, tags="filesystem"), "guard")
    assert _errors(_case(GUARD_CASE, tags=[1]), "guard")


def test_secret_in_state_is_rejected_because_the_repo_is_public():
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    case = _case(GUARD_CASE, state=f"aws s3 ls --access-key {secret}")
    assert any("segredo" in e for e in _errors(case, "guard"))


# ---------------------------------------------------------------- guard specifics


def test_guard_rules_catch_is_required_and_must_match_the_rules():
    missing = {k: v for k, v in GUARD_CASE.items() if k != "rules_catch"}
    assert any("rules_catch" in e for e in _errors(missing, "guard"))
    wrong = _case(GUARD_CASE, state="rm -rf /", should_block=True, rules_catch=False)
    assert any("rules_catch" in e for e in _errors(wrong, "guard"))
    right = _case(GUARD_CASE, state="rm -rf /", should_block=True, rules_catch=True)
    assert _errors(right, "guard") == []


def test_guard_case_blocked_by_rules_but_labeled_safe_is_inconsistent():
    case = _case(GUARD_CASE, state="rm -rf /", should_block=False, rules_catch=True)
    assert any("regra" in e for e in _errors(case, "guard"))


def test_guard_accepts_man_page_source_and_rejects_provenance():
    assert _errors(_case(GUARD_CASE, label_source="man_page"), "guard") == []
    assert _errors(_case(GUARD_CASE, provenance=DIFF_CASE["provenance"]), "guard")


# ---------------------------------------------------------------- diff specifics


def test_diff_state_must_look_like_a_diff():
    assert _errors(_case(DIFF_CASE, state="just some prose"), "diff")


def test_diff_rejects_rules_catch():
    assert _errors(_case(DIFF_CASE, rules_catch=False), "diff")


def test_outcome_diff_requires_provenance():
    case = {k: v for k, v in DIFF_CASE.items() if k != "provenance"}
    assert any("provenance" in e for e in _errors(case, "diff"))


def test_synthetic_diff_does_not_need_provenance():
    case = {k: v for k, v in DIFF_CASE.items() if k != "provenance"}
    case["label_source"] = "synthetic"
    assert _errors(case, "diff") == []


@pytest.mark.parametrize(
    "patch",
    [
        {"commit": "abc123"},
        {"commit": "Z" * 40},
        {"license": "GPL-3.0-only"},
        {"license": "proprietary"},
        {"repo": "not a repo"},
        {"url": "ftp://example.com/x"},
    ],
)
def test_provenance_is_validated(patch):
    prov = {**DIFF_CASE["provenance"], **patch}
    assert _errors(_case(DIFF_CASE, provenance=prov), "diff")


def test_provenance_missing_key_is_reported():
    prov = {k: v for k, v in DIFF_CASE["provenance"].items() if k != "license"}
    assert any("license" in e for e in _errors(_case(DIFF_CASE, provenance=prov), "diff"))


# ---------------------------------------------------------------- review workflow


def test_unreviewed_must_not_carry_a_second_label():
    assert _errors(_case(GUARD_CASE, second_label=False), "guard")


def test_agreed_requires_second_label_equal_to_primary():
    ok = _case(GUARD_CASE, review_status="agreed", second_label=False)
    assert _errors(ok, "guard") == []
    bad = _case(GUARD_CASE, review_status="agreed", second_label=True)
    assert _errors(bad, "guard")
    none = _case(GUARD_CASE, review_status="agreed", second_label=None)
    assert _errors(none, "guard")


def test_disputed_requires_second_label_different_from_primary():
    ok = _case(GUARD_CASE, review_status="disputed", second_label=True)
    assert _errors(ok, "guard") == []
    bad = _case(GUARD_CASE, review_status="disputed", second_label=False)
    assert _errors(bad, "guard")


def test_resolved_requires_resolver_and_second_label():
    ok = _case(GUARD_CASE, review_status="resolved", second_label=True, resolved_by="maintainer")
    assert _errors(ok, "guard") == []
    assert _errors(_case(GUARD_CASE, review_status="resolved", second_label=True, resolved_by=None), "guard")
    assert _errors(_case(GUARD_CASE, review_status="resolved", second_label=None, resolved_by="m"), "guard")


def test_resolved_by_is_only_allowed_when_resolved():
    assert _errors(_case(GUARD_CASE, resolved_by="maintainer"), "guard")


def test_is_final_only_for_agreed_or_resolved():
    assert not schema.is_final(GUARD_CASE)
    assert schema.is_final(_case(GUARD_CASE, review_status="agreed", second_label=False))
    assert schema.is_final(_case(GUARD_CASE, review_status="resolved", second_label=True, resolved_by="m"))
    assert not schema.is_final(_case(GUARD_CASE, review_status="disputed", second_label=True))


# ---------------------------------------------------------------- load_cases


def _write(tmp_path, payload, name="cases.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _doc(cases, surface="guard", version=1):
    return {"schema_version": version, "surface": surface, "cases": cases}


def test_load_cases_returns_validated_cases(tmp_path):
    path = _write(tmp_path, _doc([GUARD_CASE]))
    assert schema.load_cases(path) == [GUARD_CASE]


def test_load_cases_rejects_wrong_schema_version(tmp_path):
    with pytest.raises(schema.CalibrationDataError):
        schema.load_cases(_write(tmp_path, _doc([GUARD_CASE], version=2)))


def test_load_cases_rejects_unknown_surface(tmp_path):
    with pytest.raises(schema.CalibrationDataError):
        schema.load_cases(_write(tmp_path, _doc([GUARD_CASE], surface="triage")))


def test_load_cases_rejects_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(schema.CalibrationDataError):
        schema.load_cases(str(path))


def test_load_cases_reports_every_problem_with_the_case_id(tmp_path):
    broken = [_case(GUARD_CASE, id="guard-fs-001", state=" "), _case(GUARD_CASE, id="guard-fs-002", should_block=1)]
    with pytest.raises(schema.CalibrationDataError) as exc:
        schema.load_cases(_write(tmp_path, _doc(broken)))
    message = str(exc.value)
    assert "guard-fs-001" in message and "guard-fs-002" in message


def test_load_cases_rejects_duplicate_ids_and_states(tmp_path):
    same_id = [GUARD_CASE, _case(GUARD_CASE, state="rm -rf dist")]
    with pytest.raises(schema.CalibrationDataError):
        schema.load_cases(_write(tmp_path, _doc(same_id)))
    same_state = [GUARD_CASE, _case(GUARD_CASE, id="guard-fs-002")]
    with pytest.raises(schema.CalibrationDataError):
        schema.load_cases(_write(tmp_path, _doc(same_state)))


def test_load_cases_require_final_rejects_unreviewed(tmp_path):
    path = _write(tmp_path, _doc([GUARD_CASE]))
    with pytest.raises(schema.CalibrationDataError):
        schema.load_cases(path, require_final=True)
    final = _case(GUARD_CASE, review_status="agreed", second_label=False)
    assert schema.load_cases(_write(tmp_path, _doc([final]), "final.json"), require_final=True) == [final]


def test_load_cases_does_not_mutate_or_share_the_input(tmp_path):
    loaded = schema.load_cases(_write(tmp_path, _doc([GUARD_CASE])))
    loaded[0]["should_block"] = True
    assert GUARD_CASE["should_block"] is False


# ---------------------------------------------------------------- load_dir + summarize


def test_load_dir_rejects_duplicate_states_across_files(tmp_path):
    _write(tmp_path, _doc([GUARD_CASE]), "a.json")
    _write(tmp_path, _doc([_case(GUARD_CASE, id="guard-fs-002")]), "b.json")
    with pytest.raises(schema.CalibrationDataError):
        schema.load_dir(str(tmp_path))


def test_load_dir_groups_cases_by_surface(tmp_path):
    _write(tmp_path, _doc([GUARD_CASE]), "g.json")
    _write(tmp_path, _doc([DIFF_CASE], surface="diff"), "d.json")
    loaded = schema.load_dir(str(tmp_path))
    assert [c["id"] for c in loaded["guard"]] == ["guard-fs-001"]
    assert [c["id"] for c in loaded["diff"]] == ["diff-oss-001"]


def test_summarize_counts_classes_sources_and_review():
    cases = [
        GUARD_CASE,
        _case(GUARD_CASE, id="guard-fs-002", state="rm -rf /", should_block=True, rules_catch=True),
        _case(GUARD_CASE, id="guard-fs-003", state="dd if=x of=/dev/null", should_block=True, rules_catch=False),
    ]
    summary = schema.summarize(cases)
    assert summary["total"] == 3
    assert summary["should_block"] == {"true": 2, "false": 1}
    assert summary["label_source"] == {"synthetic": 3}
    assert summary["review_status"] == {"unreviewed": 3}
    assert summary["rules_catch"] == {"true": 1, "false": 2}
    assert summary["blockable_missed_by_rules"] == 1


# ---------------------------------------------------------------- seed data


def test_seed_data_is_valid_and_has_both_classes():
    loaded = schema.load_dir(DATA_DIR)
    guard = loaded["guard"]
    assert len(guard) >= 60
    summary = schema.summarize(guard)
    assert summary["should_block"]["true"] >= 25
    assert summary["should_block"]["false"] >= 30
    # The model only matters where the rules stay silent: the seed must contain such cases.
    assert summary["blockable_missed_by_rules"] >= 10


# ---------------------------------------------------------------- review hardening


@pytest.mark.parametrize(
    "field,value",
    [
        ("label_evidence", "uses key " + "AKIA" + "ABCDEFGHIJKLMNOP"),
        ("tags", ["contact" + "@example.com"]),
        ("label_evidence", "Authorization: Basic dXNlcjpwYXNz"),
        ("state", "mysql -u root -pS3cretPassw0rd -e 'select 1'"),
        ("state", "docker login -u me -p MyPassw0rdXYZ"),
        ("state", "export TOKEN=" + "sk-" + "a" * 24),
        ("state", "ssh deploy@10.0.3.14"),
        ("state", "ls /home/maria/projects"),
        ("state", "psql -c \"ALTER ROLE app PASSWORD 'x'\"; password=hunter2(abcdef)"),
    ],
)
def test_sensitive_content_is_rejected_in_every_text_field(field, value):
    case = _case(GUARD_CASE, **{field: value})
    assert any("segredo" in e for e in _errors(case, "guard")), field


def test_sensitive_content_in_provenance_is_rejected():
    prov = {**DIFF_CASE["provenance"], "repo": "owner/name"}
    case = _case(DIFF_CASE, provenance=prov, label_evidence="reported by someone" + "@example.org")
    assert any("segredo" in e for e in _errors(case, "diff"))


def test_resolved_by_must_be_a_handle_not_an_email_or_name():
    base = {"review_status": "resolved", "second_label": True}
    assert _errors(_case(GUARD_CASE, resolved_by="maintainer-1", **base), "guard") == []
    assert _errors(_case(GUARD_CASE, resolved_by="Jane Doe", **base), "guard")
    assert _errors(_case(GUARD_CASE, resolved_by="jane" + "@example.com", **base), "guard")


@pytest.mark.parametrize("field", ["label_source", "review_status", "id", "state", "should_block"])
def test_unhashable_values_are_reported_not_raised(field):
    assert _errors(_case(GUARD_CASE, **{field: ["x"]}), "guard")
    assert _errors(_case(GUARD_CASE, **{field: {"a": 1}}), "guard")


def test_load_cases_reports_unhashable_id_instead_of_crashing(tmp_path):
    broken = [_case(GUARD_CASE, id=["x"]), _case(GUARD_CASE, id="guard-fs-002", state={"a": 1})]
    with pytest.raises(schema.CalibrationDataError):
        schema.load_cases(_write(tmp_path, _doc(broken)))


def test_non_utf8_file_is_a_calibration_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(schema.CalibrationDataError):
        schema.load_cases(str(path))


def test_duplicate_json_keys_are_rejected(tmp_path):
    path = tmp_path / "dup.json"
    path.write_text('{"schema_version": 1, "schema_version": 1, "surface": "guard", "cases": []}', encoding="utf-8")
    with pytest.raises(schema.CalibrationDataError):
        schema.load_cases(str(path))


def test_boolean_schema_version_is_rejected(tmp_path):
    with pytest.raises(schema.CalibrationDataError):
        schema.load_cases(_write(tmp_path, _doc([GUARD_CASE], version=True)))


def test_id_with_trailing_newline_is_rejected():
    assert _errors(_case(GUARD_CASE, id="guard-fs-001\n"), "guard")


def test_duplicate_state_ignores_whitespace_differences(tmp_path):
    spaced = _case(GUARD_CASE, id="guard-fs-002", state="rm   -rf node_modules ")
    with pytest.raises(schema.CalibrationDataError):
        schema.load_cases(_write(tmp_path, _doc([GUARD_CASE, spaced])))


@pytest.mark.parametrize(
    "patch",
    [
        {"repo": "../.."},
        {"url": "https://evil.example.com/x"},
        {"url": "https://github.com/other/repo/commit/" + SHA},
        {"url": "https://github.com/owner/name/commit/" + "f" * 40},
        {"extra": "x"},
    ],
)
def test_provenance_must_be_consistent(patch):
    prov = {**DIFF_CASE["provenance"], **patch}
    assert _errors(_case(DIFF_CASE, provenance=prov), "diff")


def test_diff_shape_needs_a_hunk_header_and_a_new_file_line():
    assert _errors(_case(DIFF_CASE, state="prose\n@@ not a diff"), "diff")
    assert _errors(_case(DIFF_CASE, state="diff --git a/x b/x\nindex 1..2\n"), "diff")


def test_load_dir_rejects_unexpected_files_and_empty_directories(tmp_path):
    with pytest.raises(schema.CalibrationDataError):
        schema.load_dir(str(tmp_path))
    _write(tmp_path, _doc([GUARD_CASE]), "a.json")
    (tmp_path / "b.JSON").write_text("{}", encoding="utf-8")
    with pytest.raises(schema.CalibrationDataError):
        schema.load_dir(str(tmp_path))


def test_load_dir_ignores_readme_and_accepts_valid_files(tmp_path):
    _write(tmp_path, _doc([GUARD_CASE]), "a.json")
    (tmp_path / "README.md").write_text("# notes", encoding="utf-8")
    assert len(schema.load_dir(str(tmp_path))["guard"]) == 1


def test_load_dir_rejects_duplicate_ids_across_surfaces(tmp_path):
    _write(tmp_path, _doc([GUARD_CASE]), "g.json")
    _write(tmp_path, _doc([_case(DIFF_CASE, id="guard-fs-001")], surface="diff"), "d.json")
    with pytest.raises(schema.CalibrationDataError):
        schema.load_dir(str(tmp_path))


def test_load_dir_require_final_rejects_unreviewed(tmp_path):
    _write(tmp_path, _doc([GUARD_CASE]), "a.json")
    with pytest.raises(schema.CalibrationDataError):
        schema.load_dir(str(tmp_path), require_final=True)


def test_rules_catch_with_safe_label_is_rejected_through_load_cases(tmp_path):
    bad = _case(GUARD_CASE, state="rm -rf /", should_block=False, rules_catch=True)
    with pytest.raises(schema.CalibrationDataError):
        schema.load_cases(_write(tmp_path, _doc([bad])))


# ---------------------------------------------------------------- CLI


def test_main_returns_zero_and_prints_summary(tmp_path, capsys):
    _write(tmp_path, _doc([GUARD_CASE]), "a.json")
    assert schema.main([str(tmp_path)]) == 0
    assert "guard:" in capsys.readouterr().out


def test_main_returns_one_on_invalid_data(tmp_path, capsys):
    _write(tmp_path, _doc([_case(GUARD_CASE, state=" ")]), "a.json")
    assert schema.main([str(tmp_path)]) == 1
    assert "invalid:" in capsys.readouterr().err


def test_main_require_final_fails_on_unreviewed(tmp_path):
    _write(tmp_path, _doc([GUARD_CASE]), "a.json")
    assert schema.main([str(tmp_path), "--require-final"]) == 1


def test_main_returns_one_for_a_missing_directory(tmp_path):
    assert schema.main([str(tmp_path / "nope")]) == 1


def test_second_label_requires_a_second_labeler_handle():
    missing = _case(GUARD_CASE, review_status="agreed", second_label=False)
    del missing["second_labeler"]
    assert any("second_labeler" in e for e in _errors(missing, "guard"))
    assert _errors(_case(GUARD_CASE, review_status="agreed", second_label=False, second_labeler="Jane Doe"), "guard")
    email = "jane" + "@example.com"
    assert _errors(_case(GUARD_CASE, review_status="agreed", second_label=False, second_labeler=email), "guard")


def test_second_labeler_without_a_second_label_is_rejected():
    assert any("second_labeler" in e for e in _errors(_case(GUARD_CASE, second_labeler="labeler-b"), "guard"))


def test_resolved_requires_the_original_primary_label_and_only_resolved_may_carry_it():
    resolved = _case(GUARD_CASE, review_status="resolved", second_label=True, resolved_by="maintainer")
    assert _errors(resolved, "guard") == []
    missing = {k: v for k, v in resolved.items() if k != "primary_label"}
    assert any("primary_label" in e for e in _errors(missing, "guard"))
    assert _errors(_case(resolved, primary_label="yes"), "guard")
    assert any("primary_label" in e for e in _errors(_case(GUARD_CASE, primary_label=True), "guard"))


def test_second_rationale_needs_a_second_label_and_is_bounded_and_scanned():
    ok = _case(GUARD_CASE, review_status="agreed", second_label=False, second_rationale="scoped to one directory")
    assert _errors(ok, "guard") == []
    assert _errors(_case(GUARD_CASE, second_rationale="why"), "guard")
    assert _errors(_case(ok, second_rationale="x" * (schema.MAX_RATIONALE_CHARS + 1)), "guard")
    assert _errors(_case(ok, second_rationale="   "), "guard")
    leaked = "uses " + "AKIA" + "ABCDEFGHIJKLMNOP"
    assert any("segredo" in e for e in _errors(_case(ok, second_rationale=leaked), "guard"))
