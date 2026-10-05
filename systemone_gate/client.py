"""
Client library for communicating with Ollama System One API.
Supports Nimble (9B), Tev1 (0.8B, 4B) and Jev-compatible decision endpoints.
"""

import copy
import json
import math
import os
import socket
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

from .guard_rules import evaluate_command
from .rubrics import (
    RUBRIC_DIFF_RISK,
    RUBRIC_ERROR_TRIAGE,
    RUBRIC_COMMAND_SAFETY,
    RUBRIC_AGENT_ROUTING,
)

RULES_VERDICT_ANSWERS = {
    "is_destructive": {
        "type": "choice",
        "choice": "destructive_or_risky",
        "probabilities": {"safe": 0.0, "destructive_or_risky": 1.0},
        "confidence": 1.0,
    },
    "danger_score": {
        "type": "score",
        "score": 2.0,
        "probabilities": {"0": 0.0, "1": 0.0, "2": 1.0},
        "confidence": 1.0,
    },
}

DEFAULT_ENDPOINT = os.environ.get("OLLAMA_SYSTEMONE_URL", "http://localhost:11434/v1/systemone")

DEFAULT_TIMEOUT = 30.0
TIMEOUT_ENV_VAR = "SYSTEMONE_TIMEOUT"
HTTP_ERROR_BODY_CAP = 500  # bytes of the server's error body kept in messages


def _validate_timeout(value: Any, source: str) -> float:
    """Returns a positive finite float or raises ValueError naming the source."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{source} inválido: {value!r} (esperado número positivo de segundos)") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{source} inválido: {value!r} (esperado número positivo de segundos)")
    return number


def _resolve_timeout(explicit: Optional[float]) -> float:
    """constructor arg -> env SYSTEMONE_TIMEOUT -> DEFAULT_TIMEOUT."""
    if explicit is not None:
        return _validate_timeout(explicit, "timeout")
    raw = os.environ.get(TIMEOUT_ENV_VAR)
    if raw is None:
        return DEFAULT_TIMEOUT
    return _validate_timeout(raw, TIMEOUT_ENV_VAR)


def _http_error_text(err: urllib.error.HTTPError) -> str:
    """Server-provided error text: JSON "error" string if any, else a bounded raw snippet."""
    try:
        raw = err.read(HTTP_ERROR_BODY_CAP)
    except Exception:
        return ""
    snippet = raw.decode("utf-8", errors="replace").strip()
    try:
        parsed = json.loads(snippet)
    except ValueError:
        return snippet
    if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
        return parsed["error"]
    return snippet


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return True
    return isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, (socket.timeout, TimeoutError))

class SystemOneClient:
    """
    High-level Python client for Ollama System One.
    Provides methods for error triage, diff assessment, and command safety guarding.
    """

    def __init__(self, endpoint: str = DEFAULT_ENDPOINT, default_model: str = "nimble", fast_model: str = "tev1:0.8b",
                 timeout: Optional[float] = None):
        self.timeout = _resolve_timeout(timeout)  # fail fast on invalid SYSTEMONE_TIMEOUT
        self.endpoint = endpoint
        self.default_model = default_model
        self.fast_model = fast_model

    def evaluate(self, state: str, questions: Dict[str, Any], model: Optional[str] = None,
                 timeout: Optional[float] = None) -> Dict[str, Any]:
        """
        Sends an evaluation request to the System One endpoint.
        Returns the parsed response dictionary containing 'answers' and 'usage'.
        """
        selected_model = model or self.default_model
        payload = json.dumps({
            "model": selected_model,
            "state": state,
            "questions": questions
        }).encode("utf-8")

        req = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json"}
        )

        effective_timeout = self.timeout if timeout is None else timeout
        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except urllib.error.HTTPError as e:  # before URLError: HTTPError is a subclass
            return self._http_error(e, selected_model)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._error("Resposta inválida do Ollama (JSON malformado)", "invalid_response", selected_model)
        except Exception as e:
            if _is_timeout(e):
                return self._error(
                    f"Timeout após {effective_timeout:g}s aguardando {self.endpoint} "
                    f"(no primeiro uso o modelo pode estar carregando; aumente {TIMEOUT_ENV_VAR})",
                    "timeout", selected_model)
            if isinstance(e, urllib.error.URLError):
                return self._error(f"Failed to connect to Ollama at {self.endpoint}: {e}", "connection", selected_model)
            return self._error(f"Unexpected error during evaluation: {e}", "unexpected", selected_model)

    @staticmethod
    def _error(message: str, kind: str, model: str, **extra: Any) -> Dict[str, Any]:
        return {"error": message, "model": model, "error_kind": kind, **extra}

    def _http_error(self, err: urllib.error.HTTPError, model: str) -> Dict[str, Any]:
        detail = _http_error_text(err)
        message = f"Ollama respondeu HTTP {err.code} em {self.endpoint}"
        if detail:
            message += f": {detail}"
        if err.code == 404:
            message += f" (endpoint /v1/systemone não encontrado (Ollama < 0.35?) ou modelo '{model}' ausente)"
        return self._error(message, "http", model, status=err.code)

    def triage_error(self, error_text: str, model: Optional[str] = None) -> Dict[str, Any]:
        """Triages build, linker, or runtime errors using Nimble (9B)."""
        return self.evaluate(
            state=error_text,
            questions=RUBRIC_ERROR_TRIAGE,
            model=model or self.default_model
        )

    def review_diff(self, diff_text: str, model: Optional[str] = None) -> Dict[str, Any]:
        """Evaluates architectural risk and breaking changes in code diffs."""
        return self.evaluate(
            state=diff_text,
            questions=RUBRIC_DIFF_RISK,
            model=model or self.default_model
        )

    def guard_command(self, command: str, model: Optional[str] = None) -> Dict[str, Any]:
        """
        Checks if a bash command is destructive. Deterministic rules run first
        and short-circuit (no network); otherwise the fast model is consulted,
        whose verdict is a heuristic warning, not a security barrier.
        """
        match = evaluate_command(command)
        if match is not None:
            return {
                "model": "rules",
                "source": "rules",
                "rule": {"id": match.rule_id, "reason": match.reason},
                "answers": copy.deepcopy(RULES_VERDICT_ANSWERS),
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
        result = self.evaluate(
            state=command,
            questions=RUBRIC_COMMAND_SAFETY,
            model=model or self.fast_model
        )
        if isinstance(result, dict) and "error" not in result:
            return {**result, "source": "model"}
        return result

    def route_task(self, prompt: str, model: Optional[str] = None) -> Dict[str, Any]:
        """Routes a user prompt to the most suitable subagent role."""
        return self.evaluate(
            state=prompt,
            questions=RUBRIC_AGENT_ROUTING,
            model=model or self.default_model
        )
