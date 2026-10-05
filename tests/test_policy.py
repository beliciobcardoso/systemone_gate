import dataclasses
import math

import pytest

from systemone_gate.policy import (
    CommandCheck,
    Decision,
    DiffReview,
    InvalidResponse,
    PolicyConfig,
    decide_command,
    decide_diff,
    decide_on_error,
    evaluate_command,
    evaluate_diff,
    is_low_confidence,
    parse_command_check,
    parse_diff_review,
)

CFG = PolicyConfig()


def diff_res(risk=1.0, choice="safe", probs=None):
    return {
        "answers": {
            "risk_level": {"score": risk},
            "breaking_change": {
                "choice": choice,
                "probabilities": {"safe": 0.5, "breaking_change": 0.5} if probs is None else probs,
            },
        }
    }


def cmd_res(choice="safe", danger=0.1, **extra):
    res = {
        "answers": {
            "is_destructive": {"choice": choice},
            "danger_score": {"score": danger},
        }
    }
    res.update(extra)
    return res


# ---------------------------------------------------------------- parsers

def test_parse_diff_review_happy_path_ignores_extra_keys():
    res = diff_res(1.2, "breaking_change", {"breaking_change": 0.7, "safe": 0.3})
    res["coverage"] = {"truncated": True}
    res["answers"]["extra"] = {"x": 1}
    review = parse_diff_review(res)
    assert review == DiffReview(1.2, "breaking_change", {"breaking_change": 0.7, "safe": 0.3})


def test_parse_command_check_happy_path_default_source():
    assert parse_command_check(cmd_res("safe", 0.3)) == CommandCheck("safe", 0.3, "model")


def test_parse_command_check_reads_source():
    assert parse_command_check(cmd_res("x", 0, source="rules")).source == "rules"


def test_parse_accepts_int_score():
    assert parse_command_check(cmd_res("safe", 2)).danger_score == 2.0


def _mutate(res, path, value):
    node = res
    for key in path[:-1]:
        node = node[key]
    if value is KeyError:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return res


RISK = ("answers", "risk_level", "score")
BCH = ("answers", "breaking_change", "choice")
BPR = ("answers", "breaking_change", "probabilities")

DIFF_BAD = [
    ("non-dict", None),
    ("list", []),
    ("no answers", {}),
    ("answers not dict", {"answers": []}),
    ("missing risk_level", _mutate(diff_res(), ("answers", "risk_level"), KeyError)),
    ("missing breaking_change", _mutate(diff_res(), ("answers", "breaking_change"), KeyError)),
    ("missing score", _mutate(diff_res(), RISK, KeyError)),
    ("score str", _mutate(diff_res(), RISK, "1.9")),
    ("score None", _mutate(diff_res(), RISK, None)),
    ("score nan", _mutate(diff_res(), RISK, math.nan)),
    ("score inf", _mutate(diff_res(), RISK, math.inf)),
    ("score bool", _mutate(diff_res(), RISK, True)),
    ("empty choice", _mutate(diff_res(), BCH, "")),
    ("choice not str", _mutate(diff_res(), BCH, 3)),
    ("missing probs", _mutate(diff_res(), BPR, KeyError)),
    ("probs list", _mutate(diff_res(), BPR, [0.1])),
    ("probs nan", _mutate(diff_res(), BPR, {"breaking_change": math.nan})),
    ("probs str value", _mutate(diff_res(), BPR, {"breaking_change": "0.9"})),
    ("probs bool value", _mutate(diff_res(), BPR, {"breaking_change": True})),
    ("risk_level not dict", _mutate(diff_res(), ("answers", "risk_level"), 1.0)),
]


@pytest.mark.parametrize("res", [r for _, r in DIFF_BAD], ids=[i for i, _ in DIFF_BAD])
def test_parse_diff_review_rejects_malformed(res):
    with pytest.raises(InvalidResponse):
        parse_diff_review(res)


DS = ("answers", "danger_score", "score")
IC = ("answers", "is_destructive", "choice")

CMD_BAD = [
    ("non-dict", "oops"),
    ("no answers", {}),
    ("answers not dict", {"answers": None}),
    ("missing is_destructive", _mutate(cmd_res(), ("answers", "is_destructive"), KeyError)),
    ("missing danger_score", _mutate(cmd_res(), ("answers", "danger_score"), KeyError)),
    ("score str", _mutate(cmd_res(), DS, "1")),
    ("score None", _mutate(cmd_res(), DS, None)),
    ("score nan", _mutate(cmd_res(), DS, math.nan)),
    ("score inf", _mutate(cmd_res(), DS, -math.inf)),
    ("score True", _mutate(cmd_res(), DS, True)),
    ("empty choice", _mutate(cmd_res(), IC, "")),
    ("choice None", _mutate(cmd_res(), IC, None)),
    ("section not dict", _mutate(cmd_res(), ("answers", "is_destructive"), "safe")),
]


@pytest.mark.parametrize("res", [r for _, r in CMD_BAD], ids=[i for i, _ in CMD_BAD])
def test_parse_command_check_rejects_malformed(res):
    with pytest.raises(InvalidResponse):
        parse_command_check(res)


def test_invalid_response_names_field():
    with pytest.raises(InvalidResponse, match="danger_score"):
        parse_command_check(_mutate(cmd_res(), DS, "x"))


# ---------------------------------------------------------------- decide_diff

@pytest.mark.parametrize(
    "risk,breaking,action",
    [
        (1.85, 0.9, "allow"),   # risk exactly at threshold
        (1.9, 0.65, "allow"),   # breaking exactly at threshold
        (1.9, 0.7, "block"),    # both above
        (1.9, 0.2, "allow"),    # only risk above
        (1.0, 0.9, "allow"),    # only breaking above
    ],
)
def test_decide_diff_boundaries(risk, breaking, action):
    review = DiffReview(risk, "breaking_change", {"breaking_change": breaking})
    decision = decide_diff(review, CFG)
    assert decision.action == action
    assert bool(decision.reasons) == (action == "block")


def test_decide_diff_missing_breaking_prob_is_zero():
    assert decide_diff(DiffReview(2.0, "safe", {}), CFG).action == "allow"


# ---------------------------------------------------------------- decide_command

@pytest.mark.parametrize(
    "choice,danger,source,action",
    [
        ("safe", 0.0, "rules", "block"),
        ("destructive_or_risky", 0.1, "rules", "block"),
        ("destructive_or_risky", 1.51, "model", "block"),
        ("destructive_or_risky", 1.5, "model", "allow"),
        ("destructive_or_risky", 1.49, "model", "allow"),
        ("safe", 2.0, "model", "allow"),
    ],
)
def test_decide_command_table(choice, danger, source, action):
    decision = decide_command(CommandCheck(choice, danger, source), CFG)
    assert decision.action == action
    assert bool(decision.reasons) == (action == "block")


# ---------------------------------------------------------------- decide_on_error

def test_decide_on_error_allow_carries_warning():
    d = decide_on_error("diff", "boom", CFG)
    assert d == Decision("allow", (), warning="boom")


def test_decide_on_error_block():
    cfg = PolicyConfig(guard_on_error="block")
    d = decide_on_error("guard", "boom", cfg)
    assert d == Decision("block", ("boom",))
    assert decide_on_error("diff", "boom", cfg).action == "allow"


def test_decide_on_error_unknown_surface():
    with pytest.raises(ValueError):
        decide_on_error("other", "x", CFG)


# ---------------------------------------------------------------- PolicyConfig

def test_policy_config_defaults():
    assert (CFG.diff_risk_threshold, CFG.diff_breaking_threshold, CFG.guard_danger_threshold) == (1.85, 0.65, 1.5)
    assert (CFG.diff_on_error, CFG.guard_on_error) == ("allow", "allow")


def test_policy_config_validates_modes():
    with pytest.raises(ValueError):
        PolicyConfig(diff_on_error="maybe")
    with pytest.raises(ValueError):
        PolicyConfig(guard_on_error="")


def test_policy_config_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        CFG.guard_danger_threshold = 0.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        Decision("allow", ()).action = "block"


def test_from_env_empty_gives_defaults():
    assert PolicyConfig.from_env({}) == CFG


def test_from_env_partial_and_full():
    env = {
        "SYSTEMONE_GUARD_DANGER_THRESHOLD": "1.2",
        "SYSTEMONE_GUARD_ON_ERROR": "block",
    }
    cfg = PolicyConfig.from_env(env)
    assert cfg.guard_danger_threshold == 1.2 and cfg.guard_on_error == "block"
    assert cfg.diff_risk_threshold == 1.85
    full = PolicyConfig.from_env({
        "SYSTEMONE_DIFF_RISK_THRESHOLD": "1",
        "SYSTEMONE_DIFF_BREAKING_THRESHOLD": "0.5",
        "SYSTEMONE_DIFF_ON_ERROR": "block",
    })
    assert (full.diff_risk_threshold, full.diff_breaking_threshold, full.diff_on_error) == (1.0, 0.5, "block")


def test_from_env_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("SYSTEMONE_DIFF_ON_ERROR", "block")
    assert PolicyConfig.from_env().diff_on_error == "block"


@pytest.mark.parametrize(
    "var,value",
    [
        ("SYSTEMONE_DIFF_RISK_THRESHOLD", "abc"),
        ("SYSTEMONE_DIFF_BREAKING_THRESHOLD", "nan"),
        ("SYSTEMONE_GUARD_DANGER_THRESHOLD", "inf"),
        ("SYSTEMONE_DIFF_ON_ERROR", "maybe"),
        ("SYSTEMONE_GUARD_ON_ERROR", "ALLOWISH"),
    ],
)
def test_from_env_invalid_names_variable(var, value):
    with pytest.raises(ValueError, match=var):
        PolicyConfig.from_env({var: value})


# ---------------------------------------------------------------- evaluate_*

def test_evaluate_command_error_dict_allow_and_block():
    d = evaluate_command({"error": "down"}, CFG)
    assert d.action == "allow" and d.warning == "down"
    assert evaluate_command({"error": "down"}, PolicyConfig(guard_on_error="block")).action == "block"


def test_evaluate_command_malformed():
    d = evaluate_command({"answers": {}}, CFG)
    assert d.action == "allow"
    assert d.warning.startswith("resposta inválida: ")
    blocked = evaluate_command({"answers": {}}, PolicyConfig(guard_on_error="block"))
    assert blocked.action == "block" and blocked.reasons[0].startswith("resposta inválida: ")


def test_evaluate_command_valid():
    assert evaluate_command(cmd_res("destructive_or_risky", 1.9), CFG).action == "block"
    assert evaluate_command(cmd_res("safe", 0.1), CFG) == Decision("allow", ())


def test_evaluate_diff_paths():
    assert evaluate_diff({"error": "x"}, CFG).warning == "x"
    assert evaluate_diff({}, CFG).warning.startswith("resposta inválida: ")
    blocking = diff_res(1.9, "breaking_change", {"breaking_change": 0.9})
    assert evaluate_diff(blocking, CFG).action == "block"
    assert evaluate_diff(diff_res(), CFG).action == "allow"


# ---------------------------------------------------------------- min_confidence

MINC = PolicyConfig(min_confidence=0.5)
MINC_BLOCK = PolicyConfig(min_confidence=0.5, diff_on_error="block", guard_on_error="block")


def with_conf(res, **confs):
    for key, value in confs.items():
        res["answers"][key]["confidence"] = value
    return res


def test_parse_diff_confidence_is_minimum_of_present_values():
    res = with_conf(diff_res(), risk_level=0.4, breaking_change=0.2)
    assert parse_diff_review(res).confidence == 0.2
    assert parse_diff_review(with_conf(diff_res(), risk_level=0.1)).confidence == 0.1
    assert parse_diff_review(with_conf(diff_res(), breaking_change=1)).confidence == 1.0


def test_parse_diff_confidence_missing_is_none():
    assert parse_diff_review(diff_res()).confidence is None


@pytest.mark.parametrize("bad", ["0.3", float("nan"), float("inf"), True, None, -0.1, 1.5, [0.2]])
def test_parse_diff_confidence_invalid_ignored(bad):
    res = with_conf(diff_res(), risk_level=bad)
    assert parse_diff_review(res).confidence is None
    res = with_conf(diff_res(), risk_level=bad, breaking_change=0.3)
    assert parse_diff_review(res).confidence == 0.3


def test_parse_command_confidence_minimum_and_missing():
    res = with_conf(cmd_res(), is_destructive=0.25, danger_score=0.05)
    assert parse_command_check(res).confidence == 0.05
    assert parse_command_check(cmd_res()).confidence is None
    assert parse_command_check(with_conf(cmd_res(), is_destructive="x", danger_score=False)).confidence is None
    assert parse_command_check(with_conf(cmd_res(), danger_score=0.4)).confidence == 0.4


def test_confidence_tolerates_extra_keys_and_does_not_affect_equality_of_old_fields():
    res = with_conf(diff_res(1.2, "breaking_change", {"breaking_change": 0.7}), risk_level=0.2)
    res["answers"]["risk_level"]["extra"] = {"a": 1}
    review = parse_diff_review(res)
    assert (review.risk_score, review.breaking_choice) == (1.2, "breaking_change")


DIFF_GRID = [(r, b) for r in (0.0, 1.0, 1.85, 1.9, 2.0) for b in (0.0, 0.65, 0.7, 1.0)]
CMD_GRID = [(c, d, s) for c in ("safe", "destructive_or_risky") for d in (0.0, 1.5, 1.51, 2.0)
            for s in ("model", "rules")]


@pytest.mark.parametrize("risk,breaking", DIFF_GRID)
@pytest.mark.parametrize("conf", [None, 0.0, 0.03, 0.9])
def test_min_zero_diff_identical_to_previous_behavior(risk, breaking, conf):
    review = DiffReview(risk, "breaking_change", {"breaking_change": breaking}, conf)
    expected_block = risk > 1.85 and breaking > 0.65
    decision = decide_diff(review, CFG)
    assert decision.action == ("block" if expected_block else "allow")
    assert decision.warning is None
    assert decision == decide_diff(DiffReview(risk, "breaking_change", {"breaking_change": breaking}), CFG)


@pytest.mark.parametrize("choice,danger,source", CMD_GRID)
@pytest.mark.parametrize("conf", [None, 0.0, 0.03, 0.9])
def test_min_zero_command_identical_to_previous_behavior(choice, danger, source, conf):
    decision = decide_command(CommandCheck(choice, danger, source, conf), CFG)
    assert decision == decide_command(CommandCheck(choice, danger, source), CFG)
    assert decision.warning is None


def test_diff_below_min_allow_mode_allows_with_warning():
    review = DiffReview(2.0, "breaking_change", {"breaking_change": 0.9}, 0.27)
    d = decide_diff(review, MINC)
    assert d.action == "allow"
    assert d.warning == "confiança 0.27 abaixo do mínimo 0.50; veredito do modelo ignorado"


def test_diff_below_min_block_mode_blocks():
    review = DiffReview(0.1, "safe", {"breaking_change": 0.0}, 0.27)
    d = decide_diff(review, MINC_BLOCK)
    assert d.action == "block"
    assert d.reasons == ("confiança 0.27 abaixo do mínimo 0.50; veredito do modelo ignorado",)
    assert d.warning is None


def test_diff_at_or_above_min_uses_verdict():
    review = DiffReview(2.0, "breaking_change", {"breaking_change": 0.9}, 0.5)
    assert decide_diff(review, MINC).action == "block"
    assert decide_diff(DiffReview(0.1, "safe", {}, 0.5), MINC_BLOCK) == Decision("allow", ())
    assert decide_diff(DiffReview(0.1, "safe", {}, 0.49), MINC_BLOCK).action == "block"


def test_diff_unknown_confidence_uses_verdict():
    review = DiffReview(2.0, "breaking_change", {"breaking_change": 0.9}, None)
    assert decide_diff(review, MINC).action == "block"
    assert decide_diff(DiffReview(0.1, "safe", {}, None), MINC_BLOCK) == Decision("allow", ())


def test_command_below_min_modes():
    check = CommandCheck("destructive_or_risky", 1.9, "model", 0.1)
    allowed = decide_command(check, MINC)
    assert allowed.action == "allow"
    assert allowed.warning == "confiança 0.10 abaixo do mínimo 0.50; veredito do modelo ignorado"
    blocked = decide_command(CommandCheck("safe", 0.0, "model", 0.1), MINC_BLOCK)
    assert blocked.action == "block" and "abaixo do mínimo" in blocked.reasons[0]


def test_command_modes_are_per_surface():
    cfg = PolicyConfig(min_confidence=0.5, guard_on_error="block")
    assert decide_command(CommandCheck("safe", 0.0, "model", 0.1), cfg).action == "block"
    assert decide_diff(DiffReview(0.0, "safe", {}, 0.1), cfg).action == "allow"


@pytest.mark.parametrize("conf", [None, 0.0, 0.01, 1.0])
def test_rules_source_never_indeterminate(conf):
    d = decide_command(CommandCheck("destructive_or_risky", 2.0, "rules", conf), MINC)
    assert d == Decision("block", ("comando casou com regra determinística",))


def test_evaluate_applies_min_confidence_end_to_end():
    res = with_conf(cmd_res("destructive_or_risky", 1.9), is_destructive=0.2, danger_score=0.3)
    assert evaluate_command(res, CFG).action == "block"
    assert evaluate_command(res, MINC).warning.startswith("confiança 0.20")
    assert evaluate_command(res, MINC_BLOCK).action == "block"
    rules = cmd_res("destructive_or_risky", 2.0, source="rules")
    rules = with_conf(rules, is_destructive=1.0, danger_score=1.0)
    assert evaluate_command(rules, MINC).action == "block"
    dres = with_conf(diff_res(2.0, "breaking_change", {"breaking_change": 0.9}), risk_level=0.2)
    assert evaluate_diff(dres, CFG).action == "block"
    assert evaluate_diff(dres, MINC).action == "allow"
    assert evaluate_diff(dres, MINC).warning is not None


def test_is_low_confidence_helper():
    assert is_low_confidence(0.1, MINC) is True
    assert is_low_confidence(0.5, MINC) is False
    assert is_low_confidence(None, MINC) is False
    assert is_low_confidence(0.0, CFG) is False


def test_min_confidence_default_and_validation():
    assert CFG.min_confidence == 0.0
    assert PolicyConfig(min_confidence=1).min_confidence == 1
    for bad in (-0.01, 1.01, float("nan"), float("inf"), True, "0.5", None):
        with pytest.raises(ValueError, match="min_confidence"):
            PolicyConfig(min_confidence=bad)


def test_from_env_min_confidence():
    assert PolicyConfig.from_env({"SYSTEMONE_MIN_CONFIDENCE": "0.4"}).min_confidence == 0.4
    assert PolicyConfig.from_env({"SYSTEMONE_MIN_CONFIDENCE": "0"}).min_confidence == 0.0
    assert PolicyConfig.from_env({"SYSTEMONE_MIN_CONFIDENCE": "1"}).min_confidence == 1.0


@pytest.mark.parametrize("value", ["abc", "nan", "inf", "-0.1", "1.1", ""])
def test_from_env_min_confidence_invalid(value):
    with pytest.raises(ValueError, match="SYSTEMONE_MIN_CONFIDENCE"):
        PolicyConfig.from_env({"SYSTEMONE_MIN_CONFIDENCE": value})
