"""
Minimal, quote-aware shell command-line parsing used by the deterministic
guard rules. Not a full shell parser: it only needs to answer "which simple
commands would be executed, and with which arguments?".
"""

import os
import re
import shlex
from dataclasses import dataclass
from typing import List, Tuple

_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_KEYWORDS = frozenset({"{", "!", "if", "then", "do", "else", "elif", "while", "until"})
_WRAPPERS = frozenset({"sudo", "doas", "env", "nohup", "time", "command", "exec", "nice", "busybox"})
_WRAPPER_ARG_OPTIONS = {
    "sudo": frozenset({"-u", "-g", "-h", "-p", "-C", "-T", "-U", "-r", "-t"}),
    "doas": frozenset({"-u", "-C"}),
    "env": frozenset({"-u", "-C", "-S"}),
    "nice": frozenset({"-n"}),
}


MAX_SHLEX_CHARS = 20_000  # shlex is quadratic on huge tokens


@dataclass(frozen=True)
class Segment:
    """A simple command: its raw text and the operator that follows it."""
    text: str
    sep_after: str


@dataclass(frozen=True)
class SimpleCommand:
    """Normalized simple command: binary basename and its arguments."""
    name: str
    args: Tuple[str, ...]


def _skip_substitution(text: str, start: int) -> int:
    """Index right after the ')' closing a substitution whose body starts at `start`."""
    depth, i, quote = 1, start, ""
    while i < len(text):
        ch = text[i]
        if ch == "\\" and quote != "'":
            i += 2
            continue
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _skip_backtick(text: str, start: int) -> int:
    i = start
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "`":
            return i + 1
        i += 1
    return len(text)


def _body(text: str, start: int, end: int, closer: str) -> str:
    """Substitution body between `start` and `end`, dropping the closing char."""
    closed = text[end - 1:end] == closer and end - 1 >= start
    return text[start:end - 1] if closed else text[start:end]


def _operator_at(text: str, i: int) -> Tuple[str, int]:
    """Return (operator, length) when a command separator starts at i."""
    ch = text[i]
    pair = text[i:i + 2]
    if pair in ("&&", "||"):
        return pair, 2
    if pair == "|&":
        return "|", 2
    if ch == "|":
        return "|", 1
    if ch in ";\n()":
        return ";", 1
    if ch == "&":
        before = text[i - 1] if i else ""
        after = text[i + 1] if i + 1 < len(text) else ""
        if before in "<>" or after == ">":
            return "", 0
        return "&", 1
    return "", 0


def scan(command: str) -> Tuple[List[Segment], List[str]]:
    """Split into simple-command segments and executed substitutions."""
    segments: List[Segment] = []
    subs: List[str] = []
    buf: List[str] = []
    quote = ""
    i = 0

    def flush(sep: str) -> None:
        text = "".join(buf).strip()
        buf.clear()
        if text:
            segments.append(Segment(text, sep))

    while i < len(command):
        ch = command[i]
        if ch == "\\" and quote != "'":
            buf.append(command[i:i + 2])
            i += 2
            continue
        if quote == "'":
            buf.append(ch)
            quote = "" if ch == "'" else quote
            i += 1
            continue
        if ch == "$" and command[i + 1:i + 2] == "(":
            end = _skip_substitution(command, i + 2)
            subs.append(_body(command, i + 2, end, ")"))
            buf.append(command[i:end])
            i = end
            continue
        if ch in "<>" and command[i + 1:i + 2] == "(" and not quote:
            end = _skip_substitution(command, i + 2)
            buf.append(command[i:end])
            i = end
            continue
        if ch == "`":
            end = _skip_backtick(command, i + 1)
            subs.append(_body(command, i + 1, end, "`"))
            buf.append(command[i:end])
            i = end
            continue
        if quote:
            buf.append(ch)
            quote = "" if ch == quote else quote
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "#" and (not buf or buf[-1][-1:].isspace()):
            while i < len(command) and command[i] != "\n":
                i += 1
            continue
        op, length = _operator_at(command, i)
        if length:
            flush(op)
            i += length
            continue
        buf.append(ch)
        i += 1
    flush(";")
    return segments, subs


def mask_quotes(text: str) -> str:
    """Replace the content of quoted strings with spaces (quotes kept)."""
    out: List[str] = []
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and quote != "'":
            out.append("  " if quote else text[i:i + 2])
            i += 2
            continue
        if quote:
            out.append(ch if ch == quote else " ")
            quote = "" if ch == quote else quote
        else:
            quote = ch if ch in "'\"" else ""
            out.append(ch)
        i += 1
    return "".join(out)


def tokenize(segment: str) -> List[str]:
    """shlex split; on unbalanced quotes or huge input use a conservative split."""
    fallback = [t.strip("\"'") for t in segment.split()]
    if len(segment) > MAX_SHLEX_CHARS:
        return fallback
    try:
        return shlex.split(segment)
    except ValueError:
        return fallback


def _skip_wrapper_options(wrapper: str, tokens: List[str], i: int) -> int:
    takes_arg = _WRAPPER_ARG_OPTIONS.get(wrapper, frozenset())
    while i < len(tokens) and tokens[i].startswith("-"):
        option = tokens[i]
        i += 1
        if option == "--":
            break
        if option in takes_arg:
            i += 1
    return i


def normalize(tokens: List[str]) -> SimpleCommand:
    """Strip env assignments, keywords and wrappers; basename the binary."""
    i = 0
    while i < len(tokens):
        token = tokens[i]
        base = os.path.basename(token)
        if _ENV_ASSIGN.match(token) or token in _KEYWORDS:
            i += 1
        elif base in _WRAPPERS:
            i = _skip_wrapper_options(base, tokens, i + 1)
        else:
            break
    rest = tokens[i:]
    if not rest:
        return SimpleCommand("", ())
    return SimpleCommand(os.path.basename(rest[0]), tuple(rest[1:]))
