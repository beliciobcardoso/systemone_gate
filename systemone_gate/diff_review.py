"""
Per-file review of staged diffs.

Splits a ``git diff`` into one block per file, skips noise (lockfiles, binaries,
minified/generated files), truncates each file independently and aggregates the
model verdicts worst-case, reporting exactly what was and was not inspected.
Pure logic: no printing, no I/O besides the injected client.
"""

import posixpath
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

HEADER_PREFIX = "diff --git "
FALLBACK_PATH = "(diff)"
DEFAULT_MAX_LINES_PER_FILE = 250
DEFAULT_MAX_FILES = 20

REASON_LOCKFILE = "lockfile"
REASON_BINARY = "binário"
REASON_GENERATED = "minificado/gerado"
REASON_FILE_LIMIT = "limite de arquivos"

LOCKFILE_NAMES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Cargo.lock", "uv.lock", "Gemfile.lock", "go.sum", "composer.lock",
    "Pipfile.lock", "bun.lockb",
})
GENERATED_SUFFIXES = (".min.js", ".min.css", ".map")
BINARY_MARKERS = ("Binary files ", "GIT binary patch")

_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b",
                   "f": "\f", "v": "\v", '"': '"', "\\": "\\"}
_QUOTED_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"')
_OCTAL = re.compile(r"[0-7]{3}")


@dataclass(frozen=True)
class FileDiff:
    path: str
    text: str


def _unquote(body: str) -> str:
    """Decodes git's C-style quoting (octal bytes are UTF-8)."""
    out = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\" or i + 1 >= len(body):
            out += ch.encode("utf-8")
            i += 1
            continue
        octal = _OCTAL.match(body, i + 1)
        if octal:
            out.append(int(octal.group(), 8))
            i += 4
            continue
        nxt = body[i + 1]
        out += _SIMPLE_ESCAPES.get(nxt, "\\" + nxt).encode("utf-8")
        i += 2
    return out.decode("utf-8", errors="replace")


def _strip_side(token: str) -> str:
    """Removes the a/ or b/ prefix from an already unquoted token."""
    return token[2:] if token[:2] in ("a/", "b/") else token


def _parse_quoted_header(rest: str) -> Optional[str]:
    first = _QUOTED_TOKEN.match(rest)
    if not first:
        # unquoted "a/x" followed by quoted "b/y"
        head, _, tail = rest.partition(" ")
        second = _QUOTED_TOKEN.fullmatch(tail)
        return _strip_side(_unquote(second.group(1))) if second else None
    tail = rest[first.end():].lstrip(" ")
    second = _QUOTED_TOKEN.fullmatch(tail)
    if second:
        return _strip_side(_unquote(second.group(1)))
    return _strip_side(tail) if tail else None


def _symmetric_path(rest: str) -> Optional[str]:
    """Header "a/X b/X" (no rename): X may contain spaces."""
    length = (len(rest) - 5) // 2
    if length <= 0 or not rest.startswith("a/"):
        return None
    path = rest[2:2 + length]
    return path if rest == f"a/{path} b/{path}" else None


def _parse_path(header_line: str) -> str:
    rest = header_line[len(HEADER_PREFIX):].rstrip("\r")
    if '"' in rest:
        parsed = _parse_quoted_header(rest)
        if parsed:
            return parsed
    same = _symmetric_path(rest)
    if same is not None:
        return same
    idx = rest.rfind(" b/")
    if idx != -1:
        return rest[idx + 3:]
    return rest


def split_diff_by_file(diff_text: str) -> Tuple[FileDiff, ...]:
    """One FileDiff per ``diff --git`` block; text before the first header is dropped."""
    files: List[FileDiff] = []
    current: List[str] = []
    for line in diff_text.splitlines():
        if line.startswith(HEADER_PREFIX):
            if current:
                files.append(FileDiff(_parse_path(current[0]), "\n".join(current)))
            current = [line]
        elif current:
            current.append(line)
    if current:
        files.append(FileDiff(_parse_path(current[0]), "\n".join(current)))
    return tuple(files)


def ignore_reason(fd: FileDiff) -> Optional[str]:
    name = posixpath.basename(fd.path)
    if name in LOCKFILE_NAMES:
        return REASON_LOCKFILE
    if name.endswith(GENERATED_SUFFIXES):
        return REASON_GENERATED
    if any(line.startswith(BINARY_MARKERS) for line in fd.text.splitlines()):
        return REASON_BINARY
    return None


def _truncate(text: str, max_lines: int) -> Tuple[str, bool]:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, False
    marker = f"... [truncado {len(lines) - max_lines} linhas]"
    return "\n".join(lines[:max_lines] + [marker]), True


def _empty_result(coverage: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "answers": {
            "risk_level": {"score": 0.0},
            "breaking_change": {"choice": "safe", "probabilities": {}},
        },
        "coverage": coverage,
    }


def _breaking_prob(answers: Dict[str, Any]) -> float:
    probs = (answers.get("breaking_change") or {}).get("probabilities") or {}
    return probs.get("breaking_change", 0.0)


def _risk_score(answers: Dict[str, Any]) -> float:
    return (answers.get("risk_level") or {}).get("score", 0.0)


def _aggregate(per_file_answers: List[Dict[str, Any]]) -> Dict[str, Any]:
    riskiest = max(per_file_answers, key=_risk_score)
    breakiest = max(per_file_answers, key=_breaking_prob)
    return {
        "risk_level": dict(riskiest.get("risk_level") or {"score": 0.0}),
        "breaking_change": dict(breakiest.get("breaking_change") or {"choice": "safe"}),
    }


def _plan(files: Tuple[FileDiff, ...], max_files: int):
    to_review: List[FileDiff] = []
    skipped: List[Dict[str, str]] = []
    for fd in files:
        reason = ignore_reason(fd)
        if reason is None and len(to_review) >= max_files:
            reason = REASON_FILE_LIMIT
        if reason is None:
            to_review.append(fd)
        else:
            skipped.append({"path": fd.path, "reason": reason})
    return to_review, skipped


def review_staged(client: Any, diff_text: str, model: str,
                  max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
                 max_files: int = DEFAULT_MAX_FILES) -> Dict[str, Any]:
    """Reviews each non-ignored file separately and aggregates worst-case."""
    if not diff_text.strip():
        return _empty_result({"reviewed": [], "skipped": [], "truncated": []})
    files = split_diff_by_file(diff_text) or (FileDiff(FALLBACK_PATH, diff_text),)
    to_review, skipped = _plan(files, max_files)

    answers_list: List[Dict[str, Any]] = []
    truncated: List[str] = []
    for fd in to_review:
        text, was_truncated = _truncate(fd.text, max_lines_per_file)
        if was_truncated:
            truncated.append(fd.path)
        res = client.review_diff(text, model=model)
        if isinstance(res, dict) and "error" in res:
            return res
        answers = res.get("answers") if isinstance(res, dict) else None
        if not isinstance(answers, dict):
            return {"error": f"resposta inválida do modelo para {fd.path}"}
        answers_list.append(answers)

    coverage = {
        "reviewed": [fd.path for fd in to_review],
        "skipped": skipped,
        "truncated": truncated,
    }
    if not answers_list:
        return _empty_result(coverage)
    return {"answers": _aggregate(answers_list), "coverage": coverage}


def format_coverage(coverage: Dict[str, Any]) -> Tuple[str, ...]:
    """Human-readable coverage summary lines for the CLI."""
    skipped = coverage.get("skipped", [])
    truncated = coverage.get("truncated", [])
    reasons = list(dict.fromkeys(s["reason"] for s in skipped))
    ignored_part = f"{len(skipped)} ({', '.join(reasons)})" if skipped else "0"
    lines = [
        f"📁 Arquivos avaliados: {len(coverage.get('reviewed', []))}"
        f" · ignorados: {ignored_part} · truncados: {len(truncated)}"
    ]
    lines.extend(f"   - ignorado: {s['path']} ({s['reason']})" for s in skipped)
    lines.extend(f"   - truncado: {path}" for path in truncated)
    return tuple(lines)
