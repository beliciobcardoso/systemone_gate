"""
Backend diagnostics for SystemOne Gate (`systemone-gate doctor`).

Validates, in order: Ollama reachability, version, required models and a tiny
contract smoke test against /v1/systemone. Never raises on network problems:
every failure becomes a `fail` check.
"""

import json
import re
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from .client import SystemOneClient, _http_error_text, _is_timeout

MIN_OLLAMA_VERSION = "0.35.0"
DEFAULT_MODELS = ("tev1:0.8b", "nimble")
DEFAULT_CHECK_TIMEOUT = 5.0
DEFAULT_SMOKE_TIMEOUT = 60.0
DECISION_CAPABILITY = "decision"
DEFAULT_SYSTEMONE_PATH = "/v1/systemone"
EXIT_FAILED = 1
EXIT_CONFIG_ERROR = 2

OK = "ok"
WARN = "warn"
FAIL = "fail"
_ICONS = {OK: "✅", WARN: "⚠️ ", FAIL: "❌"}

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?")

SMOKE_RUBRIC = {
    "ping": {
        "type": "choice",
        "instructions": "Is the statement true? Statement: 1 + 1 = 2",
        "criteria": {"yes": None, "no": None},
    }
}


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    message: str
    hint: str = ""


@dataclass(frozen=True)
class DoctorReport:
    checks: Tuple[Check, ...]

    @property
    def ok(self) -> bool:
        return all(c.status != FAIL for c in self.checks)


def _split_endpoint(endpoint: str) -> Tuple[str, str]:
    parts = urlsplit(endpoint)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"Invalid endpoint: {endpoint!r} (expected http(s)://host[:port]/...)")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}", parts.path


def derive_base_url(endpoint: str) -> str:
    """scheme://host[:port] of the endpoint, without credentials, path or query."""
    return _split_endpoint(endpoint)[0]


def parse_version(raw: Any) -> Optional[Tuple[int, int, int]]:
    """'0.35.1-rc1' -> (0, 35, 1); None when unparsable."""
    if not isinstance(raw, str):
        return None
    match = _VERSION_RE.match(raw.strip())
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def _normalize_tag(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


def _get_json(url: str, timeout: float) -> Tuple[Optional[Dict[str, Any]], Optional[str], bool]:
    """(data, error_message, is_connection_problem). Never raises."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # before URLError: it is a subclass
        detail = _http_error_text(e)
        return None, f"HTTP {e.code} at {url}" + (f": {detail}" if detail else ""), False
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, f"Invalid response from {url} (malformed JSON)", False
    except Exception as e:
        if _is_timeout(e) or isinstance(e, socket.timeout):
            return None, f"Timeout after {timeout:g}s waiting for {url}", True
        return None, f"Failed to connect to {url}: {e}", True
    if not isinstance(data, dict):
        return None, f"Invalid response from {url} (expected a JSON object)", False
    return data, None, False


def check_reachability(base: str, timeout: float) -> Tuple[Check, Optional[Dict[str, Any]]]:
    data, error, is_connection = _get_json(f"{base}/api/version", timeout)
    if data is not None:
        return Check("reachability", OK, f"Ollama answered at {base}"), data
    if is_connection:
        hint = "Start Ollama (`ollama serve`) and check OLLAMA_SYSTEMONE_URL."
    else:
        hint = (f"The server at {base} does not look like a compatible Ollama "
                f"(>= {MIN_OLLAMA_VERSION}); check the URL and the version.")
    return Check("reachability", FAIL, error or "Unknown failure", hint), None


def check_version(version_payload: Dict[str, Any]) -> Check:
    raw = version_payload.get("version")
    parsed = parse_version(raw)
    if parsed is None:
        return Check("version", WARN, f"Unrecognized Ollama version: {raw!r}",
                     f"Confirm manually that it is >= {MIN_OLLAMA_VERSION} (`ollama --version`).")
    minimum = parse_version(MIN_OLLAMA_VERSION)
    if minimum is not None and parsed < minimum:
        return Check("version", FAIL, f"Ollama {raw} is older than the minimum {MIN_OLLAMA_VERSION}",
                     f"Update Ollama to >= {MIN_OLLAMA_VERSION} "
                     "(the /v1/systemone endpoint does not exist before that).")
    return Check("version", OK, f"Ollama {raw} (minimum {MIN_OLLAMA_VERSION})")


def _tags_by_name(payload: Dict[str, Any]) -> Optional[Dict[str, Dict[str, Any]]]:
    models = payload.get("models")
    if not isinstance(models, list):
        return None
    return {_normalize_tag(m["name"]): m for m in models if isinstance(m, dict) and isinstance(m.get("name"), str)}


def check_models(base: str, models: Sequence[str], timeout: float) -> Check:
    data, error, _ = _get_json(f"{base}/api/tags", timeout)
    if data is None:
        return Check("models", FAIL, error or "Unknown failure", "Check that Ollama is healthy.")
    installed = _tags_by_name(data)
    if installed is None:
        return Check("models", FAIL, "/api/tags response without a valid 'models' list",
                     "Ollama version possibly incompatible.")
    missing = [m for m in models if _normalize_tag(m) not in installed]
    if missing:
        return Check("models", FAIL, "Missing models: " + ", ".join(missing),
                     "; ".join(f"ollama pull {m}" for m in missing))
    no_capability = [
        m for m in models if DECISION_CAPABILITY not in (installed[_normalize_tag(m)].get("capabilities") or [])
    ]
    if no_capability:
        return Check("models", WARN, f"Capability '{DECISION_CAPABILITY}' not reported: " + ", ".join(no_capability),
                     "Older Ollama versions may not report capabilities; the contract test confirms real use.")
    return Check("models", OK, "Available models: " + ", ".join(models))


def _smoke_problem(result: Any) -> Optional[str]:
    """First contract violation found in a /v1/systemone response, or None."""
    if not isinstance(result, dict):
        return "response is not a JSON object"
    if "error" in result:
        return str(result["error"])
    answers = result.get("answers")
    if not isinstance(answers, dict):
        return "field 'answers' missing or not an object"
    answer = answers.get("ping")
    if not isinstance(answer, dict):
        return "field 'answers.ping' missing or not an object"
    if not isinstance(answer.get("choice"), str):
        return "field 'answers.ping.choice' missing or not a string"
    if not isinstance(answer.get("probabilities"), dict):
        return "field 'answers.ping.probabilities' missing or not an object"
    return None


def check_smoke(endpoint: str, model: str, timeout: float) -> Check:
    client = SystemOneClient(endpoint=endpoint, timeout=timeout)
    result = client.evaluate("1 + 1 = 2", SMOKE_RUBRIC, model=model)
    problem = _smoke_problem(result)
    if problem is None:
        return Check("smoke", OK, f"/v1/systemone answered in the expected format (model {model})")
    return Check("smoke", FAIL, f"/v1/systemone contract mismatch: {problem}",
                 "Ollama may have changed the contract; report the version in use.")


def run_doctor(endpoint: str, models: Sequence[str] = DEFAULT_MODELS, smoke: bool = True,
               timeout: float = DEFAULT_CHECK_TIMEOUT,
               smoke_timeout: float = DEFAULT_SMOKE_TIMEOUT) -> DoctorReport:
    """Runs the checks in order; stops at an unreachable backend. Raises ValueError only for a malformed endpoint."""
    base, path = _split_endpoint(endpoint)
    reach, version_payload = check_reachability(base, timeout)
    checks: List[Check] = [reach]
    if version_payload is None:
        return DoctorReport(tuple(checks))
    checks.append(check_version(version_payload))
    checks.append(check_models(base, models, timeout))
    if smoke and all(c.status != FAIL for c in checks):
        checks.append(check_smoke(base + (path or DEFAULT_SYSTEMONE_PATH), models[0], smoke_timeout))
    return DoctorReport(tuple(checks))


def format_report(report: DoctorReport) -> List[str]:
    lines = []
    for check in report.checks:
        lines.append(f"{_ICONS[check.status]} {check.name}: {check.message}")
        if check.hint and check.status != OK:
            lines.append(f"     → {check.hint}")
    failed = sum(1 for c in report.checks if c.status == FAIL)
    warned = sum(1 for c in report.checks if c.status == WARN)
    if failed:
        lines.append(f"{_ICONS[FAIL]} Diagnosis: {failed} failure(s), {warned} warning(s).")
    else:
        lines.append(f"{_ICONS[OK]} Diagnosis: backend ready ({warned} warning(s)).")
    return lines


def run_cli(endpoint: str, models: Optional[Sequence[str]], smoke: bool, smoke_timeout: float) -> int:
    """Runs the doctor, prints the checklist and maps it to an exit code."""
    try:
        report = run_doctor(endpoint, tuple(models) if models else DEFAULT_MODELS, smoke=smoke,
                            smoke_timeout=smoke_timeout)
    except ValueError as e:
        print(f"❌ Invalid configuration: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    for line in format_report(report):
        print(line)
    return 0 if report.ok else EXIT_FAILED
