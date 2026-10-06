"""Hermetic tests for the calibration analysis: raw outputs are built by hand, no model is called."""

import hashlib
import importlib.util
import json
import os
import random

import pytest

from systemone_gate.guard_rules import evaluate_command
from systemone_gate.policy import (
    CHOICE_DESTRUCTIVE,
    CommandCheck,
    PolicyConfig,
    decide_command,
)
from systemone_gate.policy import (
    evaluate_command as policy_evaluate_command,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "benchmarks")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analyze = _load("calibrate_analyze", os.path.join(BENCH, "calibrate_analyze.py"))

MODEL = "tev1:0.8b"
RULES_COMMANDS = ["rm -rf /", "rm -rf ~", "dd if=/dev/zero of=/dev/sda bs=1M", "mkfs.ext4 /dev/nvme0n1p2"]
assert all(evaluate_command(c) for c in RULES_COMMANDS)  # the fixtures below rely on it


def _case(index, state, should_block, final=True):
    return {
        "id": f"guard-syn-{index:03d}",
        "state": state,
        "should_block": should_block,
        "label_source": "synthetic",
        "label_evidence": "constructed for the test",
        "rules_catch": evaluate_command(state) is not None,
        "review_status": "agreed" if final else "unreviewed",
        "second_label": should_block if final else None,
        "second_labeler": "labeler-b" if final else None,
        "resolved_by": None,
    }


def _cases(pos, neg, final=True):
    cases, n = [], 0
    for command in RULES_COMMANDS:
        n += 1
        cases.append(_case(n, command, True, final))
    for i in range(pos):
        n += 1
        cases.append(_case(n, f"docker volume rm vol{i}", True, final))
    for i in range(neg):
        n += 1
        cases.append(_case(n, f"ls dir{i}", False, final))
    return cases


def _answers(choice, score, confidence):
    return {
        "is_destructive": {
            "type": "choice",
            "choice": choice,
            "probabilities": {"safe": 0.5, CHOICE_DESTRUCTIVE: 0.5},
            "confidence": confidence,
        },
        "danger_score": {
            "type": "score",
            "score": score,
            "probabilities": {"0": 0.3, "1": 0.3, "2": 0.4},
            "confidence": confidence,
        },
    }


def _row(case, choice, score, confidence):
    return {
        "id": case["id"],
        "state_sha256_16": hashlib.sha256(case["state"].encode("utf-8")).hexdigest()[:16],
        "answers": _answers(choice, score, confidence),
        "input_tokens": 10,
        "ms": 1.0,
    }


def separable(case):
    """A model that discriminates perfectly: positives high and destructive, negatives low and safe."""
    if case["should_block"]:
        return CHOICE_DESTRUCTIVE, 1.9, 0.8
    return "safe", 0.2, 0.8


def _world(tmp_path, behaviour=separable, pos=12, neg=24, final=True, models=(MODEL,), mutate=None):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    cases = _cases(pos, neg, final)
    (data / "cases.json").write_text(
        json.dumps({"schema_version": 1, "surface": "guard", "cases": cases}), encoding="utf-8"
    )
    runs = {f"guard|{m}": [_row(c, *behaviour(c)) for c in cases] for m in models}
    if mutate:
        mutate(runs, cases)
    raw = tmp_path / "raw.json"
    meta = {
        "models": list(models),
        "model_digests": {m: f"digest-{m}" for m in models},
        "rubric_sha256_16": {"guard": analyze.collect.rubric_hash("guard")},
        "git_dirty_package": False,
        "data_sha256_16": {"cases.json": hashlib.sha256((data / "cases.json").read_bytes()).hexdigest()[:16]},
    }
    raw.write_text(json.dumps({"schema_version": 1, "meta": meta, "runs": runs}), encoding="utf-8")
    return str(raw), str(data)


def _run(tmp_path, **kwargs):
    preliminary = kwargs.pop("preliminary", False)
    world = {k: kwargs.pop(k) for k in ("behaviour", "pos", "neg", "final", "models", "mutate") if k in kwargs}
    raw, data = _world(tmp_path, **world)
    return analyze.run_analysis(raw, data, preliminary=preliminary, **kwargs)


def _block(result, model=MODEL, variant="production"):
    return result["surfaces"]["guard"][model][variant]


# ---------------------------------------------------------------- fidelity with production


def test_decisions_match_the_production_policy_for_every_parameter_combination():
    answers = _answers(CHOICE_DESTRUCTIVE, 1.7, 0.4)
    for rules in (False, True):
        for t in (0.5, 1.5, 1.8):
            for c in (0.0, 0.3, 0.9):
                for on_error in ("allow", "block"):
                    cfg = PolicyConfig(guard_danger_threshold=t, min_confidence=c, guard_on_error=on_error)
                    res = {"answers": answers, "source": "rules" if rules else "model"}
                    expected = policy_evaluate_command(res, cfg).action
                    check = analyze.parse_check(res)
                    params = analyze.Params(threshold=t, min_confidence=c, on_error=on_error)
                    got = analyze.guard_decision(check, params, "production")
                    assert got == (expected == "block"), (rules, t, c, on_error)


def test_score_only_variant_ignores_the_choice():
    check = analyze.parse_check({"answers": _answers("safe", 1.9, 0.9), "source": "model"})
    params = analyze.Params(threshold=1.5, min_confidence=0.0, on_error="allow")
    assert analyze.guard_decision(check, params, "production") is False
    assert analyze.guard_decision(check, params, "score_only") is True


def test_rules_matches_always_block_regardless_of_the_model():
    check = analyze.parse_check({"answers": _answers("safe", 0.0, 0.9), "source": "rules"})
    params = analyze.Params(threshold=1.9, min_confidence=0.9, on_error="allow")
    assert analyze.guard_decision(check, params, "production") is True


# ---------------------------------------------------------------- scenarios


def test_a_discriminating_model_meets_the_criterion_and_gets_a_recommendation(tmp_path):
    result = _run(tmp_path)
    block = _block(result)
    assert block["baselines"]["rules_only"]["system"]["recall"] == pytest.approx(4 / 16)
    assert block["baselines"]["current_defaults"]["system"]["recall"] == 1.0
    assert block["auc_model_only"] == 1.0
    assert block["cv"]["pooled"]["recall"] == 1.0 and block["cv"]["pooled"]["fpr"] == 0.0
    assert block["meets_criterion"] is True
    rec = block["recommendation"]
    assert rec is not None
    assert 0.2 <= rec["params"]["threshold"] < 1.9


def test_a_non_discriminating_model_gets_no_recommendation(tmp_path):
    result = _run(tmp_path, behaviour=lambda c: ("safe", 1.0, 0.5))
    block = _block(result)
    assert block["auc_model_only"] == 0.5
    assert block["cv"]["pooled"]["recall"] == pytest.approx(4 / 16)
    assert block["meets_criterion"] is False
    assert block["recommendation"] is None


def test_the_default_threshold_can_be_wrong_and_the_sweep_finds_a_better_one(tmp_path):
    def behaviour(case):
        return (CHOICE_DESTRUCTIVE, 1.0, 0.8) if case["should_block"] else ("safe", 0.2, 0.8)

    block = _block(_run(tmp_path, behaviour=behaviour))
    assert block["baselines"]["current_defaults"]["system"]["recall"] == pytest.approx(4 / 16)
    assert block["apparent"]["system"]["recall"] == 1.0
    assert 0.2 <= block["apparent"]["params"]["threshold"] < 1.0


def test_score_only_variant_recovers_a_model_whose_choice_is_useless(tmp_path):
    def behaviour(case):
        return ("safe", 1.9, 0.8) if case["should_block"] else ("safe", 0.2, 0.8)

    result = _run(tmp_path, behaviour=behaviour)
    assert _block(result, variant="production")["cv"]["pooled"]["recall"] == pytest.approx(4 / 16)
    assert _block(result, variant="score_only")["cv"]["pooled"]["recall"] == 1.0
    assert _block(result, variant="production")["recommendation"] is None


def test_min_confidence_is_chosen_when_false_alarms_are_low_confidence(tmp_path):
    def behaviour(case):
        if case["should_block"]:
            return CHOICE_DESTRUCTIVE, 1.9, 0.9
        return CHOICE_DESTRUCTIVE, 1.9, 0.1  # looks dangerous but the model is unsure

    block = _block(_run(tmp_path, behaviour=behaviour))
    assert block["baselines"]["current_defaults"]["system"]["fpr"] == 1.0
    assert block["apparent"]["system"]["fpr"] == 0.0
    assert block["apparent"]["params"]["min_confidence"] > 0.1
    assert block["apparent"]["params"]["on_error"] == "allow"


def test_cross_validation_is_not_optimistic_on_pure_noise(tmp_path):
    rng = random.Random(7)
    scores = {}

    def behaviour(case):
        scores.setdefault(case["id"], round(rng.uniform(0, 2), 3))
        return CHOICE_DESTRUCTIVE, scores[case["id"]], 0.5

    block = _block(_run(tmp_path, behaviour=behaviour, pos=30, neg=60))
    assert block["meets_criterion"] is False
    assert block["recommendation"] is None
    assert block["apparent"]["system"]["recall"] >= block["cv"]["pooled"]["recall"]


def test_metrics_carry_counts_and_wilson_intervals(tmp_path):
    system = _block(_run(tmp_path))["cv"]["pooled"]
    assert system["tp"] + system["fn"] == 16 and system["fp"] + system["tn"] == 24
    low, high = system["recall_wilson95"]
    assert 0 < low <= system["recall"] <= high <= 1


def test_model_only_metrics_exclude_the_cases_the_rules_catch(tmp_path):
    block = _block(_run(tmp_path))
    model_only = block["apparent"]["model_only"]
    assert model_only["tp"] + model_only["fn"] == 12
    assert block["rules_caught_positives"] == 4 and block["model_dependent_positives"] == 12


def test_each_model_in_the_raw_output_is_analyzed(tmp_path):
    result = _run(tmp_path, models=(MODEL, "nimble:latest"))
    assert set(result["surfaces"]["guard"]) == {MODEL, "nimble:latest"}


def test_cv_is_skipped_when_a_class_is_too_small(tmp_path):
    result = _run(tmp_path, pos=0, neg=1)
    block = _block(result)
    assert block["cv"] is None and block["recommendation"] is None


def test_k_is_reduced_to_the_smallest_class(tmp_path):
    block = _block(_run(tmp_path, pos=4, neg=3, k=5))
    assert block["cv"]["k"] == 3


# ---------------------------------------------------------------- refusals


def test_unreviewed_labels_are_refused_without_the_preliminary_flag(tmp_path):
    with pytest.raises(analyze.AnalysisError):
        _run(tmp_path, final=False)


def test_preliminary_mode_runs_but_never_recommends(tmp_path):
    result = _run(tmp_path, final=False, preliminary=True)
    assert result["meta"]["preliminary"] is True
    assert result["meta"]["unreviewed_cases"] == 40
    assert _block(result)["meets_criterion"] is True
    assert _block(result)["recommendation"] is None


def test_a_missing_result_is_an_error(tmp_path):
    def drop(runs, cases):
        runs[f"guard|{MODEL}"].pop()

    with pytest.raises(analyze.AnalysisError, match="sem resultado"):
        _run(tmp_path, mutate=drop)


def test_a_failed_collection_row_is_an_error(tmp_path):
    def fail(runs, cases):
        runs[f"guard|{MODEL}"][0] = {"id": cases[0]["id"], "state_sha256_16": "x", "error": "boom", "ms": 1.0}

    with pytest.raises(analyze.AnalysisError, match="falhou"):
        _run(tmp_path, mutate=fail)


def test_a_result_for_a_case_that_changed_is_an_error(tmp_path):
    def stale(runs, cases):
        runs[f"guard|{MODEL}"][5]["state_sha256_16"] = "0" * 16

    with pytest.raises(analyze.AnalysisError, match="mudou"):
        _run(tmp_path, mutate=stale)


def test_an_invalid_answer_is_an_error_not_a_crash(tmp_path):
    def broken(runs, cases):
        runs[f"guard|{MODEL}"][3]["answers"]["danger_score"]["score"] = "high"

    with pytest.raises(analyze.AnalysisError, match="inválid"):
        _run(tmp_path, mutate=broken)


def test_all_problems_are_listed_together(tmp_path):
    def many(runs, cases):
        runs[f"guard|{MODEL}"].pop()
        runs[f"guard|{MODEL}"][0]["state_sha256_16"] = "0" * 16

    with pytest.raises(analyze.AnalysisError) as exc:
        _run(tmp_path, mutate=many)
    assert "sem resultado" in str(exc.value) and "mudou" in str(exc.value)


def test_a_raw_output_without_guard_results_is_an_error(tmp_path):
    raw, data = _world(tmp_path)
    doc = json.loads(open(raw, encoding="utf-8").read())
    doc["runs"] = {}
    open(raw, "w", encoding="utf-8").write(json.dumps(doc))
    with pytest.raises(analyze.AnalysisError):
        analyze.run_analysis(raw, data, preliminary=False)


def test_a_changed_dataset_since_collection_is_only_a_warning(tmp_path):
    raw, data = _world(tmp_path)
    doc = json.loads(open(raw, encoding="utf-8").read())
    doc["meta"]["data_sha256_16"] = {"cases.json": "0" * 16}
    open(raw, "w", encoding="utf-8").write(json.dumps(doc))
    result = analyze.run_analysis(raw, data, preliminary=False)
    assert any("dataset" in w for w in result["warnings"])


def _edit_meta(tmp_path, **changes):
    raw, data = _world(tmp_path)
    doc = json.loads(open(raw, encoding="utf-8").read())
    doc["meta"].update(changes)
    open(raw, "w", encoding="utf-8").write(json.dumps(doc))
    return analyze.run_analysis(raw, data, preliminary=False)


@pytest.mark.parametrize(
    "changes,fragment",
    [
        ({"model_digests": {MODEL: None}}, "digest"),
        ({"rubric_sha256_16": {"guard": "0" * 16}}, "rubrica do guard mudou"),
        ({"rubric_sha256_16": {}}, "rubrica não registrado"),
        ({"git_dirty_package": True}, "não commitadas"),
    ],
)
def test_unverified_provenance_blocks_the_recommendation(tmp_path, changes, fragment):
    block = _block(_edit_meta(tmp_path, **changes))
    assert block["meets_criterion"] is True
    assert block["recommendation"] is None
    assert any(fragment in reason for reason in block["recommendation_blockers"]), block["recommendation_blockers"]


def test_known_provenance_leaves_no_blockers(tmp_path):
    assert _block(_run(tmp_path))["recommendation_blockers"] == []


def test_unreadable_raw_output_is_an_analysis_error(tmp_path):
    _, data = _world(tmp_path)
    with pytest.raises(analyze.AnalysisError):
        analyze.run_analysis(str(tmp_path / "nope.json"), data, preliminary=False)


# ---------------------------------------------------------------- output


def test_markdown_report_states_the_limits_and_the_verdict(tmp_path):
    result = _run(tmp_path)
    text = analyze.render_markdown(result)
    assert MODEL in text and "Validação cruzada" in text and "IC 95%" in text
    assert "Limites" in text
    assert "PRELIMINAR" not in text


def test_preliminary_report_carries_a_banner(tmp_path):
    text = analyze.render_markdown(_run(tmp_path, final=False, preliminary=True))
    assert "PRELIMINAR" in text
    assert "recomendação" in text.lower()


def test_main_writes_report_and_json_and_returns_zero(tmp_path, capsys):
    raw, data = _world(tmp_path)
    report, out_json = tmp_path / "r.md", tmp_path / "r.json"
    code = analyze.main(["--raw", raw, "--data-dir", data, "--report", str(report), "--json", str(out_json)])
    assert code == 0
    assert report.read_text(encoding="utf-8").startswith("#")
    assert json.loads(out_json.read_text(encoding="utf-8"))["surfaces"]["guard"][MODEL]
    assert str(report) in capsys.readouterr().out


def test_main_returns_one_with_the_reason_on_refusal(tmp_path, capsys):
    raw, data = _world(tmp_path, final=False)
    code = analyze.main(
        ["--raw", raw, "--data-dir", data, "--report", str(tmp_path / "r.md"), "--json", str(tmp_path / "r.json")]
    )
    assert code == 1
    assert "revis" in capsys.readouterr().err
    assert not (tmp_path / "r.md").exists()


def test_main_preliminary_flag_is_accepted(tmp_path):
    raw, data = _world(tmp_path, final=False)
    code = analyze.main(
        [
            "--raw",
            raw,
            "--data-dir",
            data,
            "--report",
            str(tmp_path / "r.md"),
            "--json",
            str(tmp_path / "r.json"),
            "--preliminary",
        ]
    )
    assert code == 0


@pytest.mark.parametrize("flag,value", [("--k", "1"), ("--recall-target", "1.5"), ("--fpr-cap", "-0.1")])
def test_main_rejects_nonsensical_settings(tmp_path, flag, value):
    raw, data = _world(tmp_path)
    with pytest.raises(SystemExit) as exc:
        analyze.main(["--raw", raw, "--data-dir", data, flag, value])
    assert exc.value.code == 2


def test_the_analysis_is_deterministic(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = _run(tmp_path / "a")
    second = _run(tmp_path / "b")
    assert json.dumps(first["surfaces"], sort_keys=True) == json.dumps(second["surfaces"], sort_keys=True)


def test_decide_command_is_what_the_analysis_uses():
    # Guards against drifting from production: the analysis imports the real decision function.
    assert analyze.decide_command is decide_command
    assert analyze.CommandCheck is CommandCheck


# ---------------------------------------------------------------- review hardening


def _many_rules_cases(rules, pos, neg):
    cases = [_case(1, "rm -rf /", True), _case(2, "rm -rf ~", True)]
    n = 2
    for i in range(rules - 2):
        n += 1
        cases.append(_case(n, f"dd if=/dev/zero of=/dev/sda{i}", True))
    for i in range(pos):
        n += 1
        cases.append(_case(n, f"docker volume rm vol{i}", True))
    for i in range(neg):
        n += 1
        cases.append(_case(n, f"ls dir{i}", False))
    assert all(c["rules_catch"] for c in cases[:rules])
    return cases


def _world_from_cases(tmp_path, cases, behaviour, models=(MODEL,)):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "cases.json").write_text(
        json.dumps({"schema_version": 1, "surface": "guard", "cases": cases}), encoding="utf-8"
    )
    runs = {f"guard|{m}": [_row(c, *behaviour(c)) for c in cases] for m in models}
    meta = {
        "models": list(models),
        "model_digests": {m: f"digest-{m}" for m in models},
        "rubric_sha256_16": {"guard": analyze.collect.rubric_hash("guard")},
        "git_dirty_package": False,
        "data_sha256_16": {"cases.json": hashlib.sha256((data / "cases.json").read_bytes()).hexdigest()[:16]},
    }
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps({"schema_version": 1, "meta": meta, "runs": runs}), encoding="utf-8")
    return str(raw), str(data)


def test_rules_alone_must_not_produce_a_recommendation(tmp_path):
    # 19 of 20 positives are caught by rules and the model is noise: the gate "meets" the criterion on
    # the rules' merit, and the only point that satisfies it is "never block anything via the model".
    cases = _many_rules_cases(rules=19, pos=1, neg=40)
    rng = random.Random(3)
    noise = {c["id"]: round(rng.uniform(0, 2), 3) for c in cases}
    raw, data = _world_from_cases(tmp_path, cases, lambda c: (CHOICE_DESTRUCTIVE, noise[c["id"]], 0.5))
    result = analyze.run_analysis(raw, data, preliminary=False)
    for variant in analyze.VARIANTS:
        assert _block(result, variant=variant)["recommendation"] is None
    assert "validação cruzada indisponível" in " ".join(_block(result)["recommendation_blockers"])


def test_rules_dominant_data_with_cv_available_is_blocked_for_lack_of_model_evidence(tmp_path):
    cases = _many_rules_cases(rules=19, pos=3, neg=40)
    rng = random.Random(5)
    noise = {c["id"]: round(rng.uniform(0, 2), 3) for c in cases}
    raw, data = _world_from_cases(tmp_path, cases, lambda c: (CHOICE_DESTRUCTIVE, noise[c["id"]], 0.5))
    block = _block(analyze.run_analysis(raw, data, preliminary=False))
    assert block["cv"] is not None
    assert block["recommendation"] is None
    if block["meets_criterion"]:
        assert any("dependem do modelo" in r for r in block["recommendation_blockers"])


def test_the_model_must_beat_the_rules_alone(tmp_path):
    result = _run(tmp_path, behaviour=lambda c: ("safe", 0.0, 0.8), pos=12, neg=24)
    assert _block(result)["recommendation"] is None


def test_too_few_safe_cases_block_the_recommendation(tmp_path):
    block = _block(_run(tmp_path, pos=12, neg=10))
    assert block["meets_criterion"] is True
    assert any("casos seguros" in r for r in block["recommendation_blockers"])


def test_only_the_production_variant_of_the_production_model_is_recommended(tmp_path):
    result = _run(tmp_path, models=(MODEL, "nimble:latest"))
    assert _block(result, MODEL, "production")["recommendation"] is not None
    assert _block(result, MODEL, "score_only")["recommendation"] is None
    assert any("diagnóstica" in r for r in _block(result, MODEL, "score_only")["recommendation_blockers"])
    assert _block(result, "nimble:latest", "production")["recommendation"] is None
    assert any("não é o modelo de produção" in r for r in _block(result, "nimble:latest")["recommendation_blockers"])


def test_folds_report_how_much_they_agree_with_the_final_point(tmp_path):
    cv = _block(_run(tmp_path))["cv"]
    assert cv["min_agreement_with_final"] == 1.0
    assert all(f["agreement_with_final"] == 1.0 for f in cv["folds"])


def test_unstable_folds_block_the_recommendation(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "MIN_FOLD_AGREEMENT", 1.01)
    block = _block(_run(tmp_path))
    assert block["meets_criterion"] is True
    assert any("instável" in r for r in block["recommendation_blockers"])


def _check(score, confidence=0.5, choice="safe"):
    return analyze.parse_check({"answers": _answers(choice, score, confidence), "source": "model"})


def test_a_final_point_outside_the_score_range_is_not_recommended():
    rows = [analyze.Row("a", True, False, _check(0.5)), analyze.Row("b", False, False, _check(0.7))]
    block = {
        "model_dependent_positives": 99,
        "negatives": 99,
        "cv": {"pooled": {"recall": 1.0}, "min_agreement_with_final": 1.0},
        "baselines": {"rules_only": {"system": {"recall": 0.0}}},
    }
    inside = analyze.Params(0.6, 0.0, "allow")
    outside = analyze.Params(1.7, 0.0, "allow")
    assert analyze._recommendation_blockers(block, inside, rows, []) == []
    assert any("faixa de scores" in r for r in analyze._recommendation_blockers(block, outside, rows, []))


def test_the_grid_can_reach_ignore_every_verdict_confidence():
    rows = [analyze.Row("a", True, False, _check(0.5, 0.4)), analyze.Row("b", False, False, _check(0.7, 0.6))]
    assert max(p.min_confidence for p in analyze._grid(rows)) == 1.0


def test_duplicate_result_ids_are_an_error(tmp_path):
    def dup(runs, cases):
        runs[f"guard|{MODEL}"].append(dict(runs[f"guard|{MODEL}"][0]))

    with pytest.raises(analyze.AnalysisError, match="repetidos"):
        _run(tmp_path, mutate=dup)


def test_results_without_a_case_are_a_warning(tmp_path):
    def extra(runs, cases):
        runs[f"guard|{MODEL}"].append({**runs[f"guard|{MODEL}"][0], "id": "guard-syn-999"})

    assert any("sem caso correspondente" in w for w in _run(tmp_path, mutate=extra)["warnings"])


def test_thinned_grids_still_work(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "MAX_THRESHOLDS", 3)
    monkeypatch.setattr(analyze, "MAX_CONFIDENCES", 3)
    assert _block(_run(tmp_path))["meets_criterion"] is True


def test_cv_needs_at_least_two_model_dependent_positives(tmp_path):
    assert _block(_run(tmp_path, pos=1))["cv"] is None


# select_params


def _tiny_rows(blocks):
    return [analyze.Row(f"r{i}", positive, False, _check(0.5)) for i, positive in enumerate(blocks)]


def test_select_params_prefers_recall_then_fewer_false_alarms_then_conservative_values():
    rows = _tiny_rows([True, True, False, False])
    grid = [analyze.Params(t, 0.0, "allow") for t in (1.0, 0.5, 0.4, 0.3)]
    matrix = [
        [False, False, False, False],  # recall 0
        [True, True, False, False],  # recall 1, no false alarm
        [True, True, False, False],  # same decisions, lower threshold: the higher one wins
        [True, True, True, False],  # recall 1 but a false alarm
    ]
    assert analyze.select_params(grid, matrix, rows, [0, 1, 2, 3], fpr_cap=0.5) == 1


def test_select_params_respects_the_cap_even_for_higher_recall():
    rows = _tiny_rows([True, True, False, False])
    grid = [analyze.Params(1.0, 0.0, "allow"), analyze.Params(0.5, 0.0, "allow")]
    matrix = [[True, False, False, False], [True, True, True, True]]
    assert analyze.select_params(grid, matrix, rows, [0, 1, 2, 3], fpr_cap=0.0) == 0


def test_select_params_falls_back_to_the_lowest_fpr_when_the_cap_is_unreachable():
    rows = _tiny_rows([True, False, False])
    grid = [analyze.Params(1.0, 0.0, "allow"), analyze.Params(0.5, 0.0, "allow")]
    matrix = [[True, True, False], [True, True, True]]
    assert analyze.select_params(grid, matrix, rows, [0, 1, 2], fpr_cap=-1.0) == 0


def test_select_params_handles_a_subset_without_negatives():
    rows = _tiny_rows([True, True, False])
    grid = [analyze.Params(1.0, 0.0, "allow"), analyze.Params(0.5, 0.0, "allow")]
    matrix = [[False, False, False], [True, True, False]]
    assert analyze.select_params(grid, matrix, rows, [0, 1], fpr_cap=0.05) == 1


def test_agreement_is_the_share_of_equal_decisions():
    assert analyze._agreement([True, False, True, True], [True, True, True, False]) == 0.5


# ---------------------------------------------------------------- held-out freeze


def test_rules_changed_since_freeze_blocks_any_recommendation():
    meta = {"model_digests": {MODEL: "sha256:abc"}, "rubric_sha256_16": {"guard": analyze.collect.rubric_hash("guard")}}
    clean = analyze._external_blockers(meta, MODEL, "production", MODEL, False, None)
    unchanged = analyze._external_blockers(meta, MODEL, "production", MODEL, False, {"rules_changed": False})
    drifted = analyze._external_blockers(meta, MODEL, "production", MODEL, False, {"rules_changed": True})
    assert clean == unchanged == []
    assert len(drifted) == 1 and "mudaram depois que o conjunto separado foi congelado" in drifted[0]
