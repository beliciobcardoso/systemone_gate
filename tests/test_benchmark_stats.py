"""Hermetic tests for the rubric-language benchmark helpers and its English rubrics."""
import importlib.util
import json
import math
import os

import pytest

from systemone_gate.rubrics import get_diff_rubric, get_triage_rubric

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "benchmarks")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stats = _load("rubric_language_stats", os.path.join(BENCH, "rubric_language_stats.py"))
rubrics_en = _load("rubrics_en", os.path.join(BENCH, "data", "rubrics_en.py"))


def test_argmax_key_picks_highest_and_first_on_tie():
    assert stats.argmax_key({"a": 0.2, "b": 0.7, "c": 0.1}) == "b"
    assert stats.argmax_key({"a": 0.5, "b": 0.5}) == "a"
    with pytest.raises(ValueError):
        stats.argmax_key({})


def test_accuracy():
    assert stats.accuracy(["a", "b", "c", "d"], ["a", "b", "x", "x"]) == 0.5
    with pytest.raises(ValueError):
        stats.accuracy([], [])
    with pytest.raises(ValueError):
        stats.accuracy(["a"], [])


def test_wilson_interval_known_values():
    low, high = stats.wilson_interval(8, 10)
    assert low == pytest.approx(0.4902, abs=1e-3)
    assert high == pytest.approx(0.9433, abs=1e-3)
    low, high = stats.wilson_interval(0, 10)
    assert low == 0.0 and high == pytest.approx(0.2775, abs=1e-3)
    low, high = stats.wilson_interval(10, 10)
    assert high == 1.0 and low == pytest.approx(0.7225, abs=1e-3)
    with pytest.raises(ValueError):
        stats.wilson_interval(1, 0)
    with pytest.raises(ValueError):
        stats.wilson_interval(11, 10)


def test_confusion_matrix_rows_true_columns_pred():
    matrix = stats.confusion_matrix(["a", "a", "b", "c"], ["a", "b", "b", "a"], ["a", "b", "c"])
    assert matrix == [[1, 1, 0], [0, 1, 0], [1, 0, 0]]


def test_macro_f1_perfect_and_partial():
    labels = ["a", "b"]
    assert stats.macro_f1(["a", "b"], ["a", "b"], labels) == 1.0
    # a: tp=1 fp=1 fn=0 -> 2/3 ; b: tp=0 fp=0 fn=1 -> 0
    assert stats.macro_f1(["a", "b"], ["a", "a"], labels) == pytest.approx(1 / 3)
    # label without support and without predictions counts as 0
    assert stats.macro_f1(["a"], ["a"], ["a", "z"]) == 0.5


def test_mean_absolute_error_and_mean():
    assert stats.mean_absolute_error([0, 1, 2], [0.5, 1, 1]) == pytest.approx(0.5)
    assert stats.mean([1.0, 3.0]) == 2.0
    assert stats.mean([]) is None


def test_paired_counts():
    counts = stats.paired_counts([True, True, False, False, True], [True, False, True, False, False])
    assert counts == {"both_right": 1, "only_a_right": 2, "only_b_right": 1, "both_wrong": 1}


def test_mcnemar_exact_known_values():
    assert stats.mcnemar_exact(0, 0) == 1.0
    assert stats.mcnemar_exact(5, 5) == 1.0
    assert stats.mcnemar_exact(0, 6) == pytest.approx(2 / 64)  # 0.03125
    assert stats.mcnemar_exact(6, 0) == pytest.approx(0.03125)
    assert stats.mcnemar_exact(2, 8) == pytest.approx(2 * 56 / 1024)  # 0.109375
    assert stats.mcnemar_exact(10, 0) == pytest.approx(2 / 1024)
    with pytest.raises(ValueError):
        stats.mcnemar_exact(-1, 2)


def test_mcnemar_exact_symmetric_and_bounded():
    for a in range(0, 12):
        for b in range(0, 12):
            p = stats.mcnemar_exact(a, b)
            assert p == stats.mcnemar_exact(b, a)
            assert 0.0 < p <= 1.0 and math.isfinite(p)


def _shape(rubric):
    shape = {}
    for key, question in rubric.items():
        criteria = question["criteria"]
        if question["type"] == "choice":
            detail = list(criteria.keys())  # choice keys are the labels: must be identical, in order
        else:
            detail = len(criteria)
        shape[key] = (question["type"], detail)
    return shape


@pytest.mark.parametrize("pt, en", [
    (get_diff_rubric("default"), rubrics_en.RUBRIC_DIFF_RISK_EN),
    (get_triage_rubric("default"), rubrics_en.RUBRIC_ERROR_TRIAGE_EN),
])
def test_english_rubrics_mirror_portuguese_structure(pt, en):
    assert list(pt.keys()) == list(en.keys())
    assert _shape(pt) == _shape(en)
    for key in pt:
        assert pt[key]["instructions"] != en[key]["instructions"]
        if pt[key]["type"] == "score":
            assert all(isinstance(text, str) and text for text in en[key]["criteria"])
            assert pt[key]["criteria"] != en[key]["criteria"]


def _load_json(name):
    with open(os.path.join(BENCH, "data", name), encoding="utf-8") as handle:
        return json.load(handle)


def test_dataset_files_balance_and_shape():
    triage = _load_json("triage_cases.json")
    diffs = _load_json("diff_cases.json")
    choices = list(get_triage_rubric("default")["root_cause"]["criteria"].keys())
    assert len(triage) >= 35
    assert len({c["id"] for c in triage}) == len(triage)
    counts = {label: sum(1 for c in triage if c["label"] == label) for label in choices}
    assert set(counts.values()) == {5}
    assert all(c["state"] and c["why"] for c in triage)
    assert len(diffs) >= 36
    assert len({c["id"] for c in diffs}) == len(diffs)
    assert {lvl: sum(1 for c in diffs if c["risk_level"] == lvl) for lvl in (0, 1, 2)} == {0: 12, 1: 12, 2: 12}
    assert {c["breaking_change"] for c in diffs} <= {"safe", "potential_break", "breaking_change"}
    assert all(c["state"] and c["why"] for c in diffs)
    assert all(len(c["state"].splitlines()) <= 25 for c in diffs)
