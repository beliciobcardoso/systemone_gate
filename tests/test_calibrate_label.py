"""Hermetic tests for the second-labeling workflow (export blind task, import labels, resolve disputes)."""

import copy
import importlib.util
import json
import os

import pytest

from systemone_gate.guard_rules import evaluate_command

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "benchmarks")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


label = _load("calibrate_label", os.path.join(BENCH, "calibrate_label.py"))

SECRET_EVIDENCE = "primary evidence that must never reach the second labeler"


def _case(index, state, should_block, **extra):
    case = {
        "id": f"guard-fs-{index:03d}",
        "state": state,
        "should_block": should_block,
        "label_source": "synthetic",
        "label_evidence": SECRET_EVIDENCE,
        "rules_catch": evaluate_command(state) is not None,
        "review_status": "unreviewed",
        "second_label": None,
        "resolved_by": None,
        "tags": ["filesystem"],
    }
    case.update(extra)
    return case


CASES = [
    _case(1, "rm -rf node_modules", False),
    _case(2, "rm -rf /", True),
    _case(3, "docker volume rm pgdata", True),
    _case(4, "ls -la", False),
]


def _write(tmp_path, cases=None, name="guard.json"):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    doc = {"schema_version": 1, "surface": "guard", "cases": copy.deepcopy(cases or CASES)}
    (data / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(data)


def _labels(labeler="labeler-b", **verdicts):
    """verdicts: case index -> bool, optionally (bool, rationale)."""
    rows = []
    for index, verdict in verdicts.items():
        value, rationale = verdict if isinstance(verdict, tuple) else (verdict, None)
        row = {"id": label.blind_id(f"guard-fs-{int(index.lstrip('c')):03d}"), "should_block": value}
        if rationale:
            row["rationale"] = rationale
        rows.append(row)
    return {"labeler": labeler, "labels": rows}


def _cases_on_disk(data_dir):
    with open(os.path.join(data_dir, "guard.json"), encoding="utf-8") as handle:
        return {c["id"]: c for c in json.load(handle)["cases"]}


# ---------------------------------------------------------------- export (blind)


def test_exported_task_never_reveals_labels_evidence_or_rules(tmp_path):
    task = label.build_task(CASES)
    text = json.dumps(task, ensure_ascii=False)
    assert SECRET_EVIDENCE not in text
    for leaked in ("rules_catch", "label_evidence", "second_label", "review_status", "filesystem"):
        assert leaked not in text, leaked
    # The answer format legitimately mentions should_block; the cases themselves must not.
    assert "should_block" not in json.dumps(task["cases"])
    assert all(set(c) == {"id", "command"} for c in task["cases"])
    assert sorted(c["command"] for c in task["cases"]) == sorted(c["state"] for c in CASES)
    assert not any(c["id"] in text for c in CASES)  # dataset ids encode topic and can follow the label


def test_blind_ids_are_opaque_deterministic_and_unique():
    ids = [label.blind_id(f"guard-fs-{i:03d}") for i in range(200)]
    assert len(set(ids)) == 200
    assert ids == [label.blind_id(f"guard-fs-{i:03d}") for i in range(200)]
    assert all(i.startswith("c-") and "guard" not in i for i in ids)


def test_the_task_order_does_not_follow_the_dataset_order():
    cases = [_case(i, f"echo {i}", i > 20) for i in range(1, 41)]
    order = [c["command"] for c in label.build_task(cases)["cases"]]
    assert order != [c["state"] for c in cases]
    labels_first_half = [c["should_block"] for c in cases if c["state"] in order[:20]]
    assert 0 < sum(labels_first_half) < 20  # the first half of the task mixes both classes


def test_exported_task_carries_the_definition_and_the_answer_format():
    task = label.build_task(CASES)
    assert "irreversibly" in task["instructions"]
    assert set(task["answer_format"]["labels"][0]) >= {"id", "should_block"}
    assert "labeler" in task["answer_format"]


def test_export_only_includes_unreviewed_guard_cases():
    cases = CASES + [_case(5, "ls", False, review_status="agreed", second_label=False, second_labeler="x-y")]
    assert sorted(c["id"] for c in label.build_task(cases)["cases"]) == sorted(label.blind_id(c["id"]) for c in CASES)


def test_export_order_is_independent_of_the_dataset_order():
    forward = label.build_task(CASES)["cases"]
    backward = label.build_task(list(reversed(CASES)))["cases"]
    assert forward == backward


# ---------------------------------------------------------------- parse_labels


def test_parse_labels_accepts_a_wellformed_document():
    labeler, labels = label.parse_labels(_labels(c1=False, c2=(True, "wipes the root")))
    assert labeler == "labeler-b"
    assert labels[label.blind_id("guard-fs-001")] == {"should_block": False, "rationale": None}
    assert labels[label.blind_id("guard-fs-002")] == {"should_block": True, "rationale": "wipes the root"}


@pytest.mark.parametrize(
    "doc",
    [
        [],
        {"labels": []},
        {"labeler": "Jane Doe", "labels": []},
        {"labeler": "labeler-b"},
        {"labeler": "labeler-b", "labels": "x"},
        {"labeler": "labeler-b", "labels": [{"id": "guard-fs-001"}]},
        {"labeler": "labeler-b", "labels": [{"id": "guard-fs-001", "should_block": "yes"}]},
        {"labeler": "labeler-b", "labels": [{"id": 1, "should_block": True}]},
        {"labeler": "labeler-b", "labels": [{"id": "a", "should_block": True}, {"id": "a", "should_block": False}]},
        {"labeler": "labeler-b", "labels": [{"id": "guard-fs-001", "should_block": True, "rationale": 3}]},
        {"labeler": "labeler-b", "labels": [{"id": "guard-fs-001", "should_block": True, "extra": 1}]},
    ],
)
def test_parse_labels_rejects_malformed_documents(doc):
    with pytest.raises(label.LabelingError):
        label.parse_labels(doc)


def test_parse_labels_lists_every_problem():
    doc = {"labeler": "labeler-b", "labels": [{"id": "a"}, {"id": "b", "should_block": "x"}]}
    with pytest.raises(label.LabelingError) as exc:
        label.parse_labels(doc)
    assert "a" in str(exc.value) and "b" in str(exc.value)


# ---------------------------------------------------------------- apply_labels


def test_matching_labels_are_agreed_and_different_ones_are_disputed():
    labeler, labels = label.parse_labels(_labels(c1=False, c2=False, c3=True, c4=True))
    new, report = label.apply_labels(CASES, labeler, labels)
    by_id = {c["id"]: c for c in new}
    assert by_id["guard-fs-001"]["review_status"] == "agreed"
    assert by_id["guard-fs-002"]["review_status"] == "disputed"  # primary True, second False
    assert by_id["guard-fs-003"]["review_status"] == "agreed"
    assert by_id["guard-fs-004"]["review_status"] == "disputed"
    assert by_id["guard-fs-002"]["second_label"] is False
    assert by_id["guard-fs-002"]["second_labeler"] == "labeler-b"
    assert report["agreed"] == 2 and report["disputed"] == 2 and report["pending"] == 0


def test_apply_labels_does_not_mutate_the_input():
    before = copy.deepcopy(CASES)
    labeler, labels = label.parse_labels(_labels(c1=False))
    label.apply_labels(CASES, labeler, labels)
    assert CASES == before


def test_unlabeled_cases_stay_unreviewed_and_are_counted_as_pending():
    labeler, labels = label.parse_labels(_labels(c1=False))
    new, report = label.apply_labels(CASES, labeler, labels)
    assert report["pending"] == 3
    assert [c["review_status"] for c in new].count("unreviewed") == 3


def test_labels_for_unknown_ids_are_an_error():
    labeler, labels = label.parse_labels(
        {"labeler": "labeler-b", "labels": [{"id": "c-unknown", "should_block": True}]}
    )
    with pytest.raises(label.LabelingError, match="c-unknown"):
        label.apply_labels(CASES, labeler, labels)


def test_cases_already_reviewed_are_never_overwritten():
    reviewed = _case(
        1, "rm -rf node_modules", False, review_status="agreed", second_label=False, second_labeler="aa-bb"
    )
    cases = [reviewed] + CASES[1:]
    labeler, labels = label.parse_labels(_labels(c1=True, c2=True))
    new, report = label.apply_labels(cases, labeler, labels)
    assert new[0] == reviewed
    assert report["skipped_already_reviewed"] == 1


def test_the_rationale_is_stored_in_the_case_trimmed_and_bounded():
    labeler, labels = label.parse_labels(_labels(c2=(False, "  root wipe is fine in a container  ")))
    new, _ = label.apply_labels(CASES, labeler, labels)
    assert next(c for c in new if c["id"] == "guard-fs-002")["second_rationale"] == "root wipe is fine in a container"
    long_text = "x" * 900
    labeler, labels = label.parse_labels(_labels(c1=(False, long_text)))
    new, _ = label.apply_labels(CASES, labeler, labels)
    stored = next(c for c in new if c["id"] == "guard-fs-001")["second_rationale"]
    assert len(stored) == label.schema.MAX_RATIONALE_CHARS and stored.endswith("…")


def test_a_blank_rationale_is_not_stored():
    labeler, labels = label.parse_labels(
        {
            "labeler": "labeler-b",
            "labels": [{"id": label.blind_id("guard-fs-001"), "should_block": False, "rationale": "  "}],
        }
    )
    new, _ = label.apply_labels(CASES, labeler, labels)
    assert "second_rationale" not in new[0]


def test_disputes_on_rule_caught_cases_are_reported():
    labeler, labels = label.parse_labels(_labels(c2=False, c3=False))
    _, report = label.apply_labels(CASES, labeler, labels)
    assert report["rule_caught_disputes"] == ["guard-fs-002"]  # "rm -rf /" is caught by a rule; docker is not


def test_a_label_that_makes_a_case_invalid_is_refused():
    # A rule blocks "rm -rf /", so a human cannot settle it as safe: the validator rejects the case.
    disputed = _case(2, "rm -rf /", True, review_status="disputed", second_label=False, second_labeler="labeler-b")
    with pytest.raises(label.LabelingError, match="não contorna regras"):
        label.resolve_cases([disputed], {"guard-fs-002": False}, "maintainer")


# ---------------------------------------------------------------- resolve


def _disputed_cases():
    labeler, labels = label.parse_labels(_labels(c1=True, c3=False))
    return label.apply_labels(CASES, labeler, labels)[0]


def test_resolve_sets_the_final_label_and_records_who_decided():
    cases = _disputed_cases()
    new = label.resolve_cases(cases, {"guard-fs-001": True}, "maintainer")
    resolved = next(c for c in new if c["id"] == "guard-fs-001")
    assert resolved["primary_label"] is False  # the primary's original verdict is kept for the audit trail
    assert resolved["should_block"] is True
    assert resolved["review_status"] == "resolved"
    assert resolved["resolved_by"] == "maintainer"
    assert resolved["second_label"] is True  # the second labeler's label is kept for the audit trail


def test_resolve_can_side_with_the_primary_label():
    new = label.resolve_cases(_disputed_cases(), {"guard-fs-003": True}, "maintainer")
    settled = next(c for c in new if c["id"] == "guard-fs-003")
    assert settled["should_block"] is True and settled["primary_label"] is True and settled["second_label"] is False


def test_resolve_only_accepts_disputed_cases():
    with pytest.raises(label.LabelingError, match="não está em disputa"):
        label.resolve_cases(_disputed_cases(), {"guard-fs-002": True}, "maintainer")
    with pytest.raises(label.LabelingError, match="não existe"):
        label.resolve_cases(_disputed_cases(), {"guard-fs-999": True}, "maintainer")


def test_resolve_requires_a_handle():
    with pytest.raises(label.LabelingError):
        label.resolve_cases(_disputed_cases(), {"guard-fs-001": True}, "Jane Doe")


# ---------------------------------------------------------------- disputes report


def test_disputes_report_shows_both_labels_and_never_model_scores():
    labeler, labels = label.parse_labels(_labels(c2=(False, "a container wipe is harmless")))
    new, _ = label.apply_labels(CASES, labeler, labels)
    text = label.render_disputes(new)
    assert "rm -rf /" in text and "a container wipe is harmless" in text
    assert SECRET_EVIDENCE in text  # the human sees why the primary labeler chose its label
    assert "score" not in text.lower()
    assert "guard-fs-002" in text and "regra determinística" in text


def test_disputes_report_without_disputes_says_so():
    assert "Nenhuma disputa" in label.render_disputes(CASES)


def test_the_report_is_derived_from_the_dataset_so_earlier_rationales_survive_a_second_import():
    first, labels = label.parse_labels(_labels(c1=(True, "first rationale")))
    after_first, _ = label.apply_labels(CASES, first, labels)
    second, labels = label.parse_labels(_labels(c3=(False, "second rationale")))
    after_second, _ = label.apply_labels(after_first, second, labels)
    text = label.render_disputes(after_second)
    assert "first rationale" in text and "second rationale" in text and "sem justificativa" not in text


def test_a_command_with_backticks_cannot_break_out_of_the_code_fence():
    case = _case(7, "echo ```` hi", False, review_status="disputed", second_label=True, second_labeler="labeler-b")
    text = label.render_disputes([case])
    assert "`````\necho ```` hi\n`````" in text
    assert label._fence("plain") == "```" and label._fence("a``b") == "```" and label._fence("a```b") == "````"


# ---------------------------------------------------------------- CLI


def test_cli_export_writes_a_blind_task_file(tmp_path):
    data = _write(tmp_path)
    out = tmp_path / "task.json"
    assert label.main(["export", "--data-dir", data, "--out", str(out)]) == 0
    task = json.loads(out.read_text(encoding="utf-8"))
    assert len(task["cases"]) == 4 and SECRET_EVIDENCE not in out.read_text(encoding="utf-8")


def test_cli_import_updates_the_dataset_and_writes_the_disputes_sheet(tmp_path, capsys):
    data = _write(tmp_path)
    labels_file = tmp_path / "labels.json"
    labels_file.write_text(json.dumps(_labels(c1=False, c2=(False, "why"), c3=True, c4=False)), encoding="utf-8")
    sheet = tmp_path / "disputes.md"
    assert label.main(["import", str(labels_file), "--data-dir", data, "--disputes", str(sheet)]) == 0
    on_disk = _cases_on_disk(data)
    assert on_disk["guard-fs-001"]["review_status"] == "agreed"
    assert on_disk["guard-fs-002"]["review_status"] == "disputed"
    assert "guard-fs-002" in sheet.read_text(encoding="utf-8")
    assert "disputed" in capsys.readouterr().out


def test_cli_import_writes_nothing_when_any_label_is_invalid(tmp_path, capsys):
    data = _write(tmp_path)
    before = (tmp_path / "data" / "guard.json").read_text(encoding="utf-8")
    labels_file = tmp_path / "labels.json"
    labels_file.write_text(json.dumps({"labeler": "labeler-b", "labels": [{"id": "nope", "should_block": True}]}))
    assert label.main(["import", str(labels_file), "--data-dir", data, "--disputes", str(tmp_path / "d.md")]) == 1
    assert (tmp_path / "data" / "guard.json").read_text(encoding="utf-8") == before
    assert "nope" in capsys.readouterr().err
    assert not (tmp_path / "d.md").exists()


def test_cli_import_rejects_an_unreadable_labels_file(tmp_path):
    data = _write(tmp_path)
    assert label.main(["import", str(tmp_path / "missing.json"), "--data-dir", data]) == 1
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert label.main(["import", str(bad), "--data-dir", data]) == 1


def test_cli_resolve_settles_disputes_and_leaves_the_rest(tmp_path):
    data = _write(tmp_path)
    labels_file = tmp_path / "labels.json"
    labels_file.write_text(json.dumps(_labels(c1=True, c3=False)), encoding="utf-8")
    label.main(["import", str(labels_file), "--data-dir", data, "--disputes", str(tmp_path / "d.md")])
    code = label.main(["resolve", "guard-fs-001=block", "guard-fs-003=allow", "--by", "maintainer", "--data-dir", data])
    assert code == 0
    on_disk = _cases_on_disk(data)
    assert on_disk["guard-fs-001"]["should_block"] is True and on_disk["guard-fs-001"]["review_status"] == "resolved"
    assert on_disk["guard-fs-003"]["should_block"] is False
    assert on_disk["guard-fs-002"]["review_status"] == "unreviewed"


@pytest.mark.parametrize("arg", ["guard-fs-001", "guard-fs-001=maybe", "=block"])
def test_cli_resolve_rejects_malformed_pairs(tmp_path, arg):
    data = _write(tmp_path)
    assert label.main(["resolve", arg, "--by", "maintainer", "--data-dir", data]) == 1


def test_cli_resolve_changes_nothing_if_one_id_is_invalid(tmp_path):
    data = _write(tmp_path)
    labels_file = tmp_path / "labels.json"
    labels_file.write_text(json.dumps(_labels(c1=True)), encoding="utf-8")
    label.main(["import", str(labels_file), "--data-dir", data, "--disputes", str(tmp_path / "d.md")])
    before = (tmp_path / "data" / "guard.json").read_text(encoding="utf-8")
    assert label.main(["resolve", "guard-fs-001=block", "guard-fs-002=block", "--by", "m-m", "--data-dir", data]) == 1
    assert (tmp_path / "data" / "guard.json").read_text(encoding="utf-8") == before


def test_cli_status_counts_reviews_and_lists_pending_disputes(tmp_path, capsys):
    data = _write(tmp_path)
    labels_file = tmp_path / "labels.json"
    labels_file.write_text(json.dumps(_labels(c1=True, c3=True)), encoding="utf-8")
    label.main(["import", str(labels_file), "--data-dir", data, "--disputes", str(tmp_path / "d.md")])
    capsys.readouterr()
    assert label.main(["status", "--data-dir", data]) == 0
    out = capsys.readouterr().out
    assert "unreviewed: 2" in out and "agreed: 1" in out and "disputed: 1" in out
    assert "guard-fs-001" in out


def test_the_written_dataset_still_validates_and_keeps_its_formatting(tmp_path):
    data = _write(tmp_path)
    labels_file = tmp_path / "labels.json"
    labels_file.write_text(json.dumps(_labels(c1=False, c2=True, c3=True, c4=False)), encoding="utf-8")
    label.main(["import", str(labels_file), "--data-dir", data, "--disputes", str(tmp_path / "d.md")])
    text = (tmp_path / "data" / "guard.json").read_text(encoding="utf-8")
    assert text.endswith("}\n") and '\n  "cases": [' in text
    loaded = label.schema.load_dir(data, require_final=True)
    assert len(loaded["guard"]) == 4


# ---------------------------------------------------------------- hardening


def _import(tmp_path, data, rows, **kwargs):
    labels_file = tmp_path / "labels.json"
    labels_file.write_text(json.dumps(rows), encoding="utf-8")
    argv = [
        "import",
        str(labels_file),
        "--data-dir",
        data,
        "--disputes",
        str(kwargs.get("disputes", tmp_path / "d.md")),
    ]
    return label.main(argv)


def test_a_failing_sheet_write_does_not_lose_the_rationales(tmp_path, capsys):
    data = _write(tmp_path)
    rows = _labels(c1=(True, "kept in the dataset"))
    assert _import(tmp_path, data, rows, disputes=tmp_path) == 1  # a directory: the sheet cannot be written
    assert "regenere" in capsys.readouterr().err
    assert _cases_on_disk(data)["guard-fs-001"]["second_rationale"] == "kept in the dataset"
    sheet = tmp_path / "later.md"
    assert label.main(["disputes", "--data-dir", data, "--disputes", str(sheet)]) == 0
    assert "kept in the dataset" in sheet.read_text(encoding="utf-8")


def test_a_second_import_keeps_the_sheet_complete(tmp_path):
    data = _write(tmp_path)
    _import(tmp_path, data, _labels(c1=(True, "first rationale")))
    _import(tmp_path, data, _labels(c3=(False, "second rationale")))
    text = (tmp_path / "d.md").read_text(encoding="utf-8")
    assert "first rationale" in text and "second rationale" in text


def test_nothing_is_written_if_staging_a_later_file_fails(tmp_path, monkeypatch):
    data = _write(tmp_path, CASES[:2], name="a.json")
    _write(tmp_path, CASES[2:], name="b.json")
    before = {n: (tmp_path / "data" / n).read_text(encoding="utf-8") for n in ("a.json", "b.json")}
    real = label._stage_json
    calls = {"n": 0}

    def flaky(path, document):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real(path, document)

    monkeypatch.setattr(label, "_stage_json", flaky)
    assert _import(tmp_path, data, _labels(c1=False, c3=True)) == 1
    assert {n: (tmp_path / "data" / n).read_text(encoding="utf-8") for n in ("a.json", "b.json")} == before
    assert sorted(p.name for p in (tmp_path / "data").iterdir()) == ["a.json", "b.json"]


def test_the_dataset_file_mode_is_preserved(tmp_path):
    data = _write(tmp_path)
    path = tmp_path / "data" / "guard.json"
    os.chmod(path, 0o644)
    _import(tmp_path, data, _labels(c1=False))
    assert oct(path.stat().st_mode & 0o777) == "0o644"


def test_outputs_inside_the_dataset_directory_are_refused(tmp_path):
    data = _write(tmp_path)
    before = (tmp_path / "data" / "guard.json").read_text(encoding="utf-8")
    assert label.main(["export", "--data-dir", data, "--out", str(tmp_path / "data" / "guard.json")]) == 1
    assert label.main(["disputes", "--data-dir", data, "--disputes", str(tmp_path / "data" / "x.md")]) == 1
    assert (tmp_path / "data" / "guard.json").read_text(encoding="utf-8") == before


def test_duplicate_ids_in_resolve_arguments_are_rejected(tmp_path):
    data = _write(tmp_path)
    assert label.main(["resolve", "guard-fs-001=block", "guard-fs-001=allow", "--by", "m-m", "--data-dir", data]) == 1


def test_labels_file_with_duplicate_json_keys_is_rejected(tmp_path, capsys):
    data = _write(tmp_path)
    path = tmp_path / "labels.json"
    path.write_text(
        '{"labeler": "labeler-b", "labels": [{"id": "x", "should_block": true, "should_block": false}]}',
        encoding="utf-8",
    )
    assert label.main(["import", str(path), "--data-dir", data, "--disputes", str(tmp_path / "d.md")]) == 1
    assert "duplicada" in capsys.readouterr().err


def test_deeply_nested_labels_file_is_a_clean_error(tmp_path):
    data = _write(tmp_path)
    path = tmp_path / "labels.json"
    path.write_text("[" * 100000, encoding="utf-8")
    assert label.main(["import", str(path), "--data-dir", data, "--disputes", str(tmp_path / "d.md")]) == 1


def test_import_warns_when_a_rule_caught_case_is_disputed(tmp_path, capsys):
    data = _write(tmp_path)
    _import(tmp_path, data, _labels(c2=False))
    assert "regra determinística" in capsys.readouterr().err


def test_resolving_a_rule_caught_dispute_to_allow_is_refused_with_a_hint(tmp_path, capsys):
    data = _write(tmp_path)
    _import(tmp_path, data, _labels(c2=False))
    capsys.readouterr()
    assert label.main(["resolve", "guard-fs-002=allow", "--by", "maintainer", "--data-dir", data]) == 1
    assert "não contorna regras" in capsys.readouterr().err
    assert _cases_on_disk(data)["guard-fs-002"]["review_status"] == "disputed"


def test_cli_resolve_keeps_the_original_primary_label_on_disk(tmp_path):
    data = _write(tmp_path)
    _import(tmp_path, data, _labels(c1=True))
    assert label.main(["resolve", "guard-fs-001=block", "--by", "maintainer", "--data-dir", data]) == 0
    case = _cases_on_disk(data)["guard-fs-001"]
    assert (case["primary_label"], case["second_label"], case["should_block"]) == (False, True, True)
