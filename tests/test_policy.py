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
