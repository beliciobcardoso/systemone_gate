"""Hermetic tests for the paired AUC comparison between two collections."""

import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "benchmarks")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare = _load("compare_collections", os.path.join(BENCH, "compare_collections.py"))
analyze = compare.analyze


def _answers(score):
    return {
        "is_destructive": {"type": "choice", "choice": "safe", "probabilities": {"safe": 1.0}, "confidence": 0.5},
        "danger_score": {"type": "score", "score": score, "probabilities": {"0": 1.0}, "confidence": 0.5},
    }


def _rows(scores, rules=()):
    """scores: list of (should_block, score); rules: indices caught by a rule."""
    return [
        analyze.Row(f"c{i}", block, i in rules, analyze.parse_check({"answers": _answers(score), "source": "model"}))
        for i, (block, score) in enumerate(scores)
    ]


SEPARABLE = [(True, 1.8 + i * 0.01) for i in range(12)] + [(False, 0.2 + i * 0.01) for i in range(12)]
NOISY = [(b, 1.0 + 0.01 * ((i * 7) % 5)) for i, (b, _) in enumerate(SEPARABLE)]


def test_identical_collections_have_a_zero_delta_and_an_interval_around_zero():
    result = compare.paired_auc_delta(_rows(SEPARABLE), _rows(SEPARABLE), resamples=300)
    assert result["delta"] == 0.0
    assert result["ci95"][0] <= 0.0 <= result["ci95"][1]
    assert result["n"] == 24


def test_a_clearly_worse_second_collection_has_a_negative_interval():
    result = compare.paired_auc_delta(_rows(SEPARABLE), _rows(NOISY), resamples=500)
    assert result["auc_a"] == 1.0 and result["auc_b"] < 0.8
    assert result["delta"] < 0 and result["ci95"][1] < 0


def test_the_result_is_deterministic_for_a_seed_and_changes_with_it():
    a, b = _rows(SEPARABLE), _rows(NOISY)
    assert compare.paired_auc_delta(a, b, 300, seed=1) == compare.paired_auc_delta(a, b, 300, seed=1)
    assert compare.paired_auc_delta(a, b, 300, seed=1)["ci95"] != compare.paired_auc_delta(a, b, 300, seed=2)["ci95"]


def test_cases_caught_by_rules_are_excluded_from_the_universe():
    rows = _rows(SEPARABLE, rules={0, 1})
    assert compare.paired_auc_delta(rows, rows, resamples=100)["n"] == 22


def test_the_two_collections_must_cover_the_same_cases_in_the_same_order():
    with pytest.raises(ValueError, match="mesmos casos"):
        compare.paired_auc_delta(_rows(SEPARABLE), _rows(SEPARABLE)[:-1] + _rows([(True, 1.0)]))


def test_both_classes_are_required_outside_the_rules():
    rows = _rows([(True, 1.0), (True, 1.1), (False, 0.1)], rules={2})
    with pytest.raises(ValueError, match="bloqueáveis e seguros"):
        compare.paired_auc_delta(rows, rows)


# ---------------------------------------------------------------- CLI


def _world(tmp_path, scores_a, scores_b):
    cases = [
        {
            "id": f"guard-t-{i:03d}",
            "state": f"docker volume rm vol{i}" if block else f"ls dir{i}",
            "should_block": block,
            "label_source": "synthetic",
            "label_evidence": "constructed",
            "rules_catch": False,
            "review_status": "unreviewed",
            "second_label": None,
            "resolved_by": None,
        }
        for i, (block, _) in enumerate(SEPARABLE)
    ]
    data = tmp_path / "data"
    data.mkdir()
    (data / "cases.json").write_text(
        json.dumps({"schema_version": 1, "surface": "guard", "cases": cases}), encoding="utf-8"
    )

    def raw(name, scores):
        rows = [
            {
                "id": c["id"],
                "state_sha256_16": analyze.collect.state_hash(c["state"]),
                "answers": _answers(score),
                "ms": 1.0,
            }
            for c, (_, score) in zip(cases, scores)
        ]
        path = tmp_path / name
        path.write_text(json.dumps({"schema_version": 1, "meta": {}, "runs": {"guard|m": rows}}), encoding="utf-8")
        return str(path)

    return raw("a.json", scores_a), raw("b.json", scores_b), str(data)


def test_main_prints_the_comparison_and_writes_json(tmp_path, capsys):
    a, b, data = _world(tmp_path, SEPARABLE, NOISY)
    out = tmp_path / "out.json"
    code = compare.main([a, b, "--labels", "pt", "en", "--data-dir", data, "--resamples", "200", "--json", str(out)])
    text = capsys.readouterr().out
    assert code == 0
    assert "m: AUC pt 1.000" in text and "delta (en - pt)" in text and "sem revisão" in text
    assert json.loads(out.read_text(encoding="utf-8"))["models"]["m"]["delta"] < 0


def test_main_fails_cleanly_on_bad_input(tmp_path, capsys):
    _, _, data = _world(tmp_path, SEPARABLE, NOISY)
    assert compare.main([str(tmp_path / "x.json"), str(tmp_path / "y.json"), "--data-dir", data]) == 1
    assert "recusado" in capsys.readouterr().err


def test_main_rejects_too_few_resamples(tmp_path):
    a, b, data = _world(tmp_path, SEPARABLE, NOISY)
    with pytest.raises(SystemExit):
        compare.main([a, b, "--data-dir", data, "--resamples", "5"])
