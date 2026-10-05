"""
Decision policy for SystemOne Gate.

Single place where model responses become allow/block verdicts, shared by the
CLI, the examples and any Python agent. Pure functions: the only I/O is
``PolicyConfig.from_env``.

NOTE: the default thresholds are NOT calibrated against real data.
"""

import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

ACTION_ALLOW = "allow"
ACTION_BLOCK = "block"
VALID_ON_ERROR = (ACTION_ALLOW, ACTION_BLOCK)

SURFACE_DIFF = "diff"
SURFACE_GUARD = "guard"

CHOICE_DESTRUCTIVE = "destructive_or_risky"
CHOICE_BREAKING = "breaking_change"
SOURCE_MODEL = "model"
SOURCE_RULES = "rules"

DEFAULT_DIFF_RISK_THRESHOLD = 1.85
DEFAULT_DIFF_BREAKING_THRESHOLD = 0.65
DEFAULT_GUARD_DANGER_THRESHOLD = 1.5

ENV_DIFF_RISK = "SYSTEMONE_DIFF_RISK_THRESHOLD"
ENV_DIFF_BREAKING = "SYSTEMONE_DIFF_BREAKING_THRESHOLD"
ENV_GUARD_DANGER = "SYSTEMONE_GUARD_DANGER_THRESHOLD"
ENV_DIFF_ON_ERROR = "SYSTEMONE_DIFF_ON_ERROR"
ENV_GUARD_ON_ERROR = "SYSTEMONE_GUARD_ON_ERROR"
ENV_MIN_CONFIDENCE = "SYSTEMONE_MIN_CONFIDENCE"

# Opt-in: 0.0 disables the check. Deliberately NOT a "good" value: there is no
# calibration data and observed confidences are low across the board.
DEFAULT_MIN_CONFIDENCE = 0.0

INVALID_RESPONSE_PREFIX = "resposta inválida: "


class InvalidResponse(ValueError):
    """The model response does not have the expected shape."""


def _validate_on_error(name: str, value: str) -> None:
    if value not in VALID_ON_ERROR:
        raise ValueError(f"{name} deve ser 'allow' ou 'block', recebido: {value!r}")


def _env_float(environ: Mapping[str, str], name: str, default: float) -> float:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} deve ser um número, recebido: {raw!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"{name} deve ser um número finito, recebido: {raw!r}")
    return value


def _env_confidence(environ: Mapping[str, str]) -> float:
    value = _env_float(environ, ENV_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{ENV_MIN_CONFIDENCE} deve estar entre 0 e 1, recebido: {environ[ENV_MIN_CONFIDENCE]!r}")
    return value


@dataclass(frozen=True)
class PolicyConfig:
    diff_risk_threshold: float = DEFAULT_DIFF_RISK_THRESHOLD
    diff_breaking_threshold: float = DEFAULT_DIFF_BREAKING_THRESHOLD
    guard_danger_threshold: float = DEFAULT_GUARD_DANGER_THRESHOLD
    diff_on_error: str = ACTION_ALLOW
    guard_on_error: str = ACTION_ALLOW
    min_confidence: float = DEFAULT_MIN_CONFIDENCE

    def __post_init__(self) -> None:
        if not _is_finite_real(self.min_confidence) or not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError(
                f"min_confidence deve ser um número finito entre 0 e 1, recebido: {self.min_confidence!r}"
            )
        _validate_on_error("diff_on_error", self.diff_on_error)
        _validate_on_error("guard_on_error", self.guard_on_error)

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "PolicyConfig":
        env = os.environ if environ is None else environ
        diff_on_error = env.get(ENV_DIFF_ON_ERROR, ACTION_ALLOW)
        guard_on_error = env.get(ENV_GUARD_ON_ERROR, ACTION_ALLOW)
        _validate_on_error(ENV_DIFF_ON_ERROR, diff_on_error)
        _validate_on_error(ENV_GUARD_ON_ERROR, guard_on_error)
        return cls(
            diff_risk_threshold=_env_float(env, ENV_DIFF_RISK, DEFAULT_DIFF_RISK_THRESHOLD),
            diff_breaking_threshold=_env_float(env, ENV_DIFF_BREAKING, DEFAULT_DIFF_BREAKING_THRESHOLD),
            guard_danger_threshold=_env_float(env, ENV_GUARD_DANGER, DEFAULT_GUARD_DANGER_THRESHOLD),
            diff_on_error=diff_on_error,
            guard_on_error=guard_on_error,
            min_confidence=_env_confidence(env),
        )


@dataclass(frozen=True)
class DiffReview:
    risk_score: float
    breaking_choice: str
    breaking_probs: Mapping[str, float] = field(default_factory=dict)
    confidence: Optional[float] = None


@dataclass(frozen=True)
class CommandCheck:
    choice: str
    danger_score: float
    source: str = SOURCE_MODEL
    confidence: Optional[float] = None


@dataclass(frozen=True)
class Decision:
    action: str
    reasons: Tuple[str, ...]
    warning: Optional[str] = None


# ---------------------------------------------------------------- parsing

def _is_finite_real(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _section(res: Any, key: str) -> Dict[str, Any]:
    if not isinstance(res, dict):
        raise InvalidResponse("resposta não é um objeto")
    answers = res.get("answers")
    if not isinstance(answers, dict):
        raise InvalidResponse("campo 'answers' ausente ou inválido")
    section = answers.get(key)
    if not isinstance(section, dict):
        raise InvalidResponse(f"campo 'answers.{key}' ausente ou inválido")
    return section


def _score(section: Dict[str, Any], key: str) -> float:
    value = section.get("score")
    if not _is_finite_real(value):
        raise InvalidResponse(f"campo 'answers.{key}.score' deve ser número finito")
    return float(value)


def _choice(section: Dict[str, Any], key: str) -> str:
    value = section.get("choice")
    if not isinstance(value, str) or not value:
        raise InvalidResponse(f"campo 'answers.{key}.choice' deve ser string não vazia")
    return value


def _probabilities(section: Dict[str, Any], key: str) -> Dict[str, float]:
    probs = section.get("probabilities")
    if not isinstance(probs, dict):
        raise InvalidResponse(f"campo 'answers.{key}.probabilities' deve ser objeto")
    for name, prob in probs.items():
        if not _is_finite_real(prob):
            raise InvalidResponse(f"probabilidade 'answers.{key}.probabilities.{name}' inválida")
    return {str(name): float(prob) for name, prob in probs.items()}


def _min_confidence(*sections: Dict[str, Any]) -> Optional[float]:
    """Lowest valid confidence in [0, 1] among the sections; None if there is none."""
    values = [
        float(c)
        for c in (section.get("confidence") for section in sections)
        if _is_finite_real(c) and 0.0 <= c <= 1.0
    ]
    return min(values) if values else None


def parse_diff_review(res: Any) -> DiffReview:
    risk = _section(res, "risk_level")
    breaking = _section(res, "breaking_change")
    return DiffReview(
        risk_score=_score(risk, "risk_level"),
        breaking_choice=_choice(breaking, "breaking_change"),
        breaking_probs=_probabilities(breaking, "breaking_change"),
        confidence=_min_confidence(risk, breaking),
    )


def parse_command_check(res: Any) -> CommandCheck:
    destructive = _section(res, "is_destructive")
    danger = _section(res, "danger_score")
    return CommandCheck(
        choice=_choice(destructive, "is_destructive"),
        danger_score=_score(danger, "danger_score"),
        source=str(res.get("source", SOURCE_MODEL)),
        confidence=_min_confidence(destructive, danger),
    )


# ---------------------------------------------------------------- decisions

def is_low_confidence(confidence: Optional[float], cfg: PolicyConfig) -> bool:
    """True when the check is enabled, the confidence is known and below the minimum."""
    return cfg.min_confidence > 0 and confidence is not None and confidence < cfg.min_confidence


def _low_confidence_message(confidence: float, cfg: PolicyConfig) -> str:
    return (
        f"confiança {confidence:.2f} abaixo do mínimo {cfg.min_confidence:.2f}; "
        "veredito do modelo ignorado"
    )


def decide_diff(review: DiffReview, cfg: PolicyConfig) -> Decision:
    if is_low_confidence(review.confidence, cfg):
        return decide_on_error(SURFACE_DIFF, _low_confidence_message(review.confidence, cfg), cfg)
    breaking_prob = review.breaking_probs.get(CHOICE_BREAKING, 0.0)
    if review.risk_score > cfg.diff_risk_threshold and breaking_prob > cfg.diff_breaking_threshold:
        reason = (
            f"risco {review.risk_score:.2f} > {cfg.diff_risk_threshold} e "
            f"probabilidade de quebra {breaking_prob:.2f} > {cfg.diff_breaking_threshold}"
        )
        return Decision(ACTION_BLOCK, (reason,))
    return Decision(ACTION_ALLOW, ())


def decide_command(check: CommandCheck, cfg: PolicyConfig) -> Decision:
    if check.source == SOURCE_RULES:
        return Decision(ACTION_BLOCK, ("comando casou com regra determinística",))
    if is_low_confidence(check.confidence, cfg):
        return decide_on_error(SURFACE_GUARD, _low_confidence_message(check.confidence, cfg), cfg)
    if check.choice == CHOICE_DESTRUCTIVE and check.danger_score > cfg.guard_danger_threshold:
        reason = f"destrutivo com perigo {check.danger_score:.2f} > {cfg.guard_danger_threshold}"
        return Decision(ACTION_BLOCK, (reason,))
    return Decision(ACTION_ALLOW, ())


def decide_on_error(surface: str, message: str, cfg: PolicyConfig) -> Decision:
    if surface == SURFACE_DIFF:
        mode = cfg.diff_on_error
    elif surface == SURFACE_GUARD:
        mode = cfg.guard_on_error
    else:
        raise ValueError(f"surface desconhecida: {surface!r}")
    if mode == ACTION_BLOCK:
        return Decision(ACTION_BLOCK, (message,))
    return Decision(ACTION_ALLOW, (), warning=message)


def _evaluate(res: Any, surface: str, parse, decide, cfg: PolicyConfig) -> Decision:
    if isinstance(res, dict) and "error" in res:
        return decide_on_error(surface, str(res["error"]), cfg)
    try:
        parsed = parse(res)
    except InvalidResponse as exc:
        return decide_on_error(surface, f"{INVALID_RESPONSE_PREFIX}{exc}", cfg)
    return decide(parsed, cfg)


def evaluate_diff(res: Any, cfg: PolicyConfig) -> Decision:
    return _evaluate(res, SURFACE_DIFF, parse_diff_review, decide_diff, cfg)


def evaluate_command(res: Any, cfg: PolicyConfig) -> Decision:
    return _evaluate(res, SURFACE_GUARD, parse_command_check, decide_command, cfg)
