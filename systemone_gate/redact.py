"""
Best-effort secret redaction for text sent to the model.

Pattern matching only: it catches well-known token formats and obvious
`key=value` assignments. It is NOT a guarantee that no secret leaves the
machine. All patterns are linear-time (no nested or ambiguous quantifiers).
"""

import re
from dataclasses import dataclass
from typing import Callable, List, Match, Pattern, Tuple

REDACTION_MARKER = "[REDACTED:%s]"
_MARKER_PREFIX = "[REDACTED:"
_MIN_ASSIGNMENT_VALUE = 8

_NOT_IN_TOKEN = r"(?<![A-Za-z0-9_])"  # token must start a word-run (keeps scanning linear)
_NOT_IN_B64URL = r"(?<![A-Za-z0-9_-])"


@dataclass(frozen=True)
class RedactionResult:
    """`findings` holds one rule id per redaction (never the matched secret)."""

    text: str
    findings: Tuple[str, ...]


def _marker(rule: str) -> str:
    return REDACTION_MARKER % rule


def _whole(rule: str) -> Callable[[Match[str]], str]:
    return lambda _m: _marker(rule)


def _keep_prefix(rule: str) -> Callable[[Match[str]], str]:
    """Keeps group 1 (context) and replaces the secret in group 2."""
    return lambda m: m.group(1) + _marker(rule)


_IDENT_CHAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")
_CODE_CHARS = frozenset("()[]{}<>")


def _looks_like_reference(value: str) -> bool:
    """Variable/template/call expressions are code, not secrets."""
    if value.startswith("$") or _IDENT_CHAIN.match(value):
        return True
    return any(ch in _CODE_CHARS for ch in value)


def _redact_assignment(m: Match[str]) -> str:
    head, value = m.group(1), m.group(2)
    if value[0] in "\"'":
        inner = value[1:-1]
        if inner.startswith(_MARKER_PREFIX) or inner.startswith(("${", "{{")):
            return m.group(0)
        return head + value[0] + _marker("secret_assignment") + value[-1]
    if _looks_like_reference(value):
        return m.group(0)
    spaced = " " in head or "\t" in head
    if spaced and not any(ch.isdigit() for ch in value):
        return m.group(0)  # `key: plainword` is usually prose/YAML schema, not a secret
    return head + _marker("secret_assignment")


_SEP = r"""["']?[ \t]{0,3}[:=][ \t]{0,3}"""
_KEYWORD = r"(?:password|passwd|secret(?:[_-]?key)?|api[_-]?key|token)"
_VALUE_CHAR = r"""[^\s"',;]"""

# Order matters: specific formats first, generic assignments last.
_RULES: Tuple[Tuple[str, Pattern[str], Callable[[Match[str]], str]], ...] = (
    ("private_key",
     re.compile(r"-----BEGIN [A-Z ]{0,30}PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]{0,30}PRIVATE KEY-----|\Z)"),
     _whole("private_key")),
    ("aws_access_key",
     re.compile(_NOT_IN_TOKEN + r"(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Za-z])"),
     _whole("aws_access_key")),
    ("aws_secret_key",
     re.compile(r"(aws[_-]?secret[_-]?(?:access[_-]?)?key" + _SEP + r"[\"']?)[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])",
                re.IGNORECASE),
     _keep_prefix("aws_secret_key")),
    ("github_token",
     re.compile(_NOT_IN_TOKEN + r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
     _whole("github_token")),
    ("slack_token",
     re.compile(_NOT_IN_TOKEN + r"xox[baprs]-[A-Za-z0-9-]{10,}"),
     _whole("slack_token")),
    ("google_api_key",
     re.compile(_NOT_IN_B64URL + r"AIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"),
     _whole("google_api_key")),
    ("stripe_key",
     re.compile(_NOT_IN_TOKEN + r"[rs]k_live_[0-9A-Za-z]{16,}"),
     _whole("stripe_key")),
    ("jwt",
     re.compile(_NOT_IN_B64URL + r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
     _whole("jwt")),
    ("bearer_token",
     re.compile(r"(Authorization[\"']?[ \t]{0,3}[:=][ \t]{0,3}[\"']?Bearer[ \t]+)[A-Za-z0-9._~+/=-]{8,}",
                re.IGNORECASE),
     _keep_prefix("bearer_token")),
    ("url_password",
     re.compile(r"(?<![A-Za-z0-9+.-])([A-Za-z][A-Za-z0-9+.-]{1,20}://[^\s:/@'\"]{1,100}:)"
                r"(?!\[REDACTED:)[^\s/@'\"]+(?=@)"),
     _keep_prefix("url_password")),
    ("secret_assignment",
     re.compile(r"(" + _KEYWORD + _SEP + r")(\"[^\"\n]{%d,}\"|'[^'\n]{%d,}'|%s{%d,}(?![^\s\"',;]))"
                % (_MIN_ASSIGNMENT_VALUE, _MIN_ASSIGNMENT_VALUE, _VALUE_CHAR, _MIN_ASSIGNMENT_VALUE),
                re.IGNORECASE),
     _redact_assignment),
)


def redact_secrets(text: str) -> RedactionResult:
    """
    Replaces likely secrets in `text` with `[REDACTED:<rule-id>]`, keeping the
    surrounding text. Idempotent and linear-time; never raises for a `str`.
    Raises TypeError for non-str input.
    """
    if not isinstance(text, str):
        raise TypeError("redact_secrets expects str, got %s" % type(text).__name__)
    findings: List[str] = []
    current = text
    for rule_id, pattern, replacer in _RULES:
        current = _apply(pattern, replacer, rule_id, current, findings)
    return RedactionResult(text=current, findings=tuple(findings))


def _apply(
    pattern: Pattern[str],
    replacer: Callable[[Match[str]], str],
    rule_id: str,
    text: str,
    findings: List[str],
) -> str:
    def substitute(m: Match[str]) -> str:
        replacement = replacer(m)
        if replacement != m.group(0):
            findings.append(rule_id)
        return replacement

    return pattern.sub(substitute, text)
