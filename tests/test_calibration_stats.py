"""Hermetic tests for the pure helpers of the calibration analysis."""

import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "benchmarks")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cstats = _load("calibration_stats", os.path.join(BENCH, "calibration_stats.py"))


# ---------------------------------------------------------------- auc


def test_auc_is_one_for_perfect_separation_and_zero_for_inverted():
    assert cstats.auc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert cstats.auc([0.1, 0.2], [0.9, 0.8]) == 0.0


def test_auc_counts_ties_as_half():
    assert cstats.auc([0.5], [0.5]) == 0.5
    assert cstats.auc([1.0, 0.5], [0.5, 0.0]) == pytest.approx((1 + 1 + 0.5 + 1) / 4)


def test_auc_of_a_constant_score_is_one_half():
    assert cstats.auc([0.3] * 5, [0.3] * 7) == 0.5


def test_auc_is_undefined_without_both_classes():
    assert cstats.auc([], [0.1]) is None
    assert cstats.auc([0.1], []) is None


# ---------------------------------------------------------------- threshold_candidates


def test_threshold_candidates_are_midpoints_plus_both_ends():
    cuts = cstats.threshold_candidates([0.0, 1.0, 1.0, 2.0])
    assert cuts == [-1.0, 0.5, 1.5, 3.0]


def test_threshold_candidates_single_value():
    assert cstats.threshold_candidates([1.0]) == [0.0, 2.0]


def test_threshold_candidates_ignore_non_finite_and_empty():
    assert cstats.threshold_candidates([float("nan"), 1.0, float("inf")]) == [0.0, 2.0]
    assert cstats.threshold_candidates([]) == []


def test_threshold_candidates_are_thinned_but_keep_both_ends():
    values = [i / 100 for i in range(100)]
    cuts = cstats.threshold_candidates(values, max_candidates=10)
    assert len(cuts) == 10
    assert cuts[0] < 0.0 and cuts[-1] > 0.99
    assert cuts == sorted(cuts)


def test_threshold_candidates_reject_a_nonsensical_limit():
    with pytest.raises(ValueError):
        cstats.threshold_candidates([1.0, 2.0], max_candidates=1)


# ---------------------------------------------------------------- stratified_folds


def _items(n_pos, n_neg):
    return [(f"p{i}", "pos") for i in range(n_pos)] + [(f"n{i}", "neg") for i in range(n_neg)]


def test_stratified_folds_are_balanced_within_each_stratum():
    folds = cstats.stratified_folds(_items(10, 20), 5)
    for stratum, prefix, size in (("pos", "p", 10), ("neg", "n", 20)):
        counts = [sum(1 for k, f in folds.items() if k.startswith(prefix) and f == fold) for fold in range(5)]
        assert counts == [size // 5] * 5, stratum


def test_stratified_folds_are_deterministic_and_cover_every_item():
    items = _items(7, 9)
    first = cstats.stratified_folds(items, 4)
    assert first == cstats.stratified_folds(list(reversed(items)), 4)
    assert set(first) == {i for i, _ in items}
    assert set(first.values()) == {0, 1, 2, 3}


def test_stratified_folds_reject_invalid_k_and_duplicate_ids():
    with pytest.raises(ValueError):
        cstats.stratified_folds(_items(3, 3), 1)
    with pytest.raises(ValueError):
        cstats.stratified_folds([("a", "x"), ("a", "y")], 2)


def test_stratified_folds_keep_sizes_within_one_even_for_small_uneven_strata():
    items = [(f"a{i}", "x") for i in range(3)] + [(f"b{i}", "y") for i in range(2)] + [(f"c{i}", "z") for i in range(4)]
    folds = cstats.stratified_folds(items, 5)
    sizes = [sum(1 for f in folds.values() if f == fold) for fold in range(5)]
    assert max(sizes) - min(sizes) <= 1
    assert sum(sizes) == 9
