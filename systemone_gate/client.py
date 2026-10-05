"""
Client library for communicating with Ollama System One API.
Supports Nimble (9B), Tev1 (0.8B, 4B) and Jev-compatible decision endpoints.
"""

import copy
import json
import math
import os
import ipaddress
import re
import socket
import sys
import urllib.parse
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, Tuple

from .guard_rules import evaluate_command
from .redact import redact_secrets
from .rubrics import (
    DEFAULT_PROFILE,
    PROFILES,
    get_diff_rubric,
    get_triage_rubric,
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
PROFILE_ENV_VAR = "SYSTEMONE_PROFILE"
HTTP_ERROR_BODY_CAP = 500  # bytes of the server's error body kept in messages
ALLOW_REMOTE_ENV_VAR = "SYSTEMONE_ALLOW_REMOTE"
REDACT_ENV_VAR = "SYSTEMONE_REDACT"
ALLOWED_SCHEMES = ("http", "https")

_remote_warned = False  # the remote-host warning is printed once per process
_USERINFO = re.compile(r"(://)[^/?#]*@")


def _safe_url(url: str) -> str:
    """URL for messages: any `user:pass@` is hidden."""
    return _USERINFO.sub(r"\1[REDACTED]@", url)


def _is_loopback_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False  # any other hostname counts as remote (no DNS resolution)
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback


def _allow_remote() -> bool:
    value = os.environ.get(ALLOW_REMOTE_ENV_VAR, "")
    return value != "" and value != "0"


def _warn_remote_once(url: str) -> None:
    global _remote_warned
    if _remote_warned:
        return
    _remote_warned = True
    print(f"[systemone_gate] AVISO: endpoint remoto {_safe_url(url)}; diffs, comandos e logs "
          f"sairão desta máquina ({ALLOW_REMOTE_ENV_VAR} ativo)", file=sys.stderr)


def _validate_endpoint(endpoint: Any) -> str:
    """Accepts http(s) endpoints on loopback (remote only with SYSTEMONE_ALLOW_REMOTE); else ValueError."""
    shown = _safe_url(endpoint) if isinstance(endpoint, str) else repr(type(endpoint).__name__)
    try:
        parts = urllib.parse.urlsplit(endpoint)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError on an invalid port
    except (ValueError, AttributeError):
        raise ValueError(f"Endpoint inválido: {shown}") from None
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValueError(f"Endpoint inválido: esquema '{parts.scheme}' não permitido em {shown} (use http ou https)")
    if not host:
        raise ValueError(f"Endpoint inválido: sem host em {shown}")
    if not _is_loopback_host(host):
        if not _allow_remote():
            raise ValueError(f"Endpoint inválido: host remoto em {shown}; "
                             f"defina {ALLOW_REMOTE_ENV_VAR}=1 para permitir")
        _warn_remote_once(endpoint)
    return endpoint


def _resolve_redact(explicit: Optional[bool]) -> bool:
    """constructor arg -> env SYSTEMONE_REDACT (`0` disables) -> ON."""
    if explicit is not None:
        return bool(explicit)
    return os.environ.get(REDACT_ENV_VAR) != "0"


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


def _resolve_profile(explicit: Optional[str]) -> str:
    """call arg -> env SYSTEMONE_PROFILE -> DEFAULT_PROFILE; ValueError names the source."""
    if explicit is not None:
        source, value = "profile", explicit
    else:
        raw = os.environ.get(PROFILE_ENV_VAR)
        if not raw:
            return DEFAULT_PROFILE
        source, value = PROFILE_ENV_VAR, raw
    if value not in PROFILES:
        raise ValueError(f"{source} inválido: {value!r} (válidos: {', '.join(PROFILES)})")
    return value


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
                 timeout: Optional[float] = None, redact: Optional[bool] = None):
        self.timeout = _resolve_timeout(timeout)  # fail fast on invalid SYSTEMONE_TIMEOUT
        self.endpoint = _validate_endpoint(endpoint)
        self.redact = _resolve_redact(redact)
        self.default_model = default_model
        self.fast_model = fast_model

    def evaluate(self, state: str, questions: Dict[str, Any], model: Optional[str] = None,
                 timeout: Optional[float] = None) -> Dict[str, Any]:
        """
        Sends an evaluation request to the System One endpoint.
        Returns the parsed response dictionary containing 'answers' and 'usage'.
        """
        selected_model = model or self.default_model
        sent_state, redactions = self._redact_state(state)
        payload = json.dumps({
            "model": selected_model,
            "state": sent_state,
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
                if redactions and isinstance(data, dict):
                    return {**data, "redacted": redactions}
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

    def _redact_state(self, state: Any) -> Tuple[Any, int]:
        """Text actually sent to the model and how many secrets were masked (best effort)."""
        if not self.redact or not isinstance(state, str):
            return state, 0
        result = redact_secrets(state)
        return result.text, len(result.findings)

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

    def triage_error(self, error_text: str, model: Optional[str] = None,
                     profile: Optional[str] = None) -> Dict[str, Any]:
        """Triages build, linker, or runtime errors using Nimble (9B).

        Raises ValueError for an unknown profile (arg or SYSTEMONE_PROFILE)."""
        return self.evaluate(
            state=error_text,
            questions=get_triage_rubric(_resolve_profile(profile)),
            model=model or self.default_model
        )

    def review_diff(self, diff_text: str, model: Optional[str] = None,
                    profile: Optional[str] = None) -> Dict[str, Any]:
        """Evaluates architectural risk and breaking changes in code diffs.

        Raises ValueError for an unknown profile (arg or SYSTEMONE_PROFILE)."""
        return self.evaluate(
            state=diff_text,
            questions=get_diff_rubric(_resolve_profile(profile)),
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
