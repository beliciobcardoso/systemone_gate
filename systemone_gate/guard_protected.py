"""
User-defined protected paths for the deterministic guard.

`SYSTEMONE_PROTECTED_PATHS` is a `:`-separated list of absolute paths (`~`, `$HOME`
and `${HOME}` are expanded at the start of an entry or a command target). A command
that would remove, move or recursively re-permission a protected path is blocked.

A protected path is the path itself, its contents (`<path>/*`) and, for recursive
operations, any ancestor (`rm -rf ~` destroys `~/Projetos`). Anything deeper than the
path stays free. Pure text analysis: no filesystem access, so symlinks are not resolved
and unresolved expansions (`$OTHER`, `~user`, backticks) are never guessed.
"""

import os
import posixpath
from dataclasses import dataclass, replace
from fnmatch import fnmatchcase
from typing import List, Mapping, Optional, Sequence, Tuple

from .guard_common import RuleMatch, has_recursive, split_args
from .shell_parse import SimpleCommand

ENV_VAR = "SYSTEMONE_PROTECTED_PATHS"
RULE_ID = "protected-path"
MAX_PATH_LEN = 4096

_HOME_FORMS = ("~", "$HOME", "${HOME}")
_CD_COMMANDS = frozenset({"cd", "pushd"})
_GLOB_CHARS = frozenset("*?[")


@dataclass(frozen=True)
class ProtectedPaths:
    entries: Tuple[str, ...]
    cwd: Optional[str]
    home: str


def _expand_home(token: str, home: str) -> Optional[str]:
    """Replace a leading `~`/`$HOME`/`${HOME}`; None when another expansion remains."""
    for form in _HOME_FORMS:
        if token == form or token.startswith(form + "/"):
            token = home + token[len(form) :]
            break
    if "$" in token or "`" in token or token.startswith("~"):
        return None
    return token


def _absolute(token: str, home: str, cwd: Optional[str]) -> Optional[str]:
    if not token or len(token) > MAX_PATH_LEN:
        return None
    expanded = _expand_home(token, home)
    if expanded is None:
        return None
    if not expanded.startswith("/"):
        if not cwd:
            return None
        expanded = cwd + "/" + expanded
    return "/" + posixpath.normpath(expanded).lstrip("/")


def _resolve_home(env: Mapping[str, str]) -> str:
    home = env.get("HOME") or os.path.expanduser("~")
    return home.rstrip("/") or "/"


def load_protected_paths(
    env: Optional[Mapping[str, str]] = None, cwd: Optional[str] = None
) -> Tuple[Optional[ProtectedPaths], List[str]]:
    """Parse `SYSTEMONE_PROTECTED_PATHS`; invalid entries are skipped and reported as warnings."""
    env = os.environ if env is None else env
    raw = env.get(ENV_VAR, "")
    home = _resolve_home(env)
    entries: List[str] = []
    warnings: List[str] = []
    for item in (part.strip() for part in raw.split(":")):
        if not item:
            continue
        resolved = _absolute(item, home, None)
        if resolved is None:
            warnings.append(f"[SystemOne Gate] Warning: {ENV_VAR} entry ignored (not an absolute path): {item}")
        elif resolved not in entries:
            entries.append(resolved)
    if not entries:
        return None, warnings
    return ProtectedPaths(tuple(entries), cwd, home), warnings


def _is_ancestor(path: str, entry: str) -> bool:
    return path == "/" or entry.startswith(path + "/")


def _glob_match(pattern: str, path: str) -> bool:
    """Component-wise fnmatch, so `*` never crosses a `/`."""
    pattern_parts = pattern.split("/")
    path_parts = path.split("/")
    return len(pattern_parts) == len(path_parts) and all(
        fnmatchcase(part, glob) for glob, part in zip(pattern_parts, path_parts)
    )


def _hit(base: str, entry: str, recursive: bool) -> bool:
    if _GLOB_CHARS & set(base):
        candidates = [entry]
        if recursive:
            parts = entry.split("/")
            candidates += ["/".join(parts[:i]) or "/" for i in range(1, len(parts))]
        return any(_glob_match(base, c) for c in candidates)
    return base == entry or (recursive and _is_ancestor(base, entry))


def advance_cwd(cmd: SimpleCommand, protected: Optional[ProtectedPaths]) -> Optional[ProtectedPaths]:
    """Track `cd`/`pushd` within one command line, so `cd ~/x && rm -rf .` sees the new directory.

    An unknown destination (`cd -`, `cd $VAR`, `popd`, `pushd +1`) clears the cwd: relative targets are
    then skipped rather than guessed. Directory changes inside conditionals are treated as taken.
    """
    if protected is None or (cmd.name not in _CD_COMMANDS and cmd.name != "popd"):
        return protected
    _, targets = split_args(cmd.args)
    first = targets[0] if targets else None
    destination: Optional[str]
    if cmd.name == "popd" or first == "-" or (cmd.name == "pushd" and (first is None or first[0] in "+-")):
        destination = None
    elif first is None:
        destination = protected.home
    else:
        destination = _absolute(first, protected.home, protected.cwd)
    return replace(protected, cwd=destination)


def _protected_entry(target: str, recursive: bool, protected: ProtectedPaths) -> Optional[str]:
    if target == "*":
        target = "."  # a bare glob in the cwd is the cwd's contents
    stripped = target[:-2] if target.endswith("/*") and len(target) > 2 else target
    base = _absolute(stripped, protected.home, protected.cwd)
    if base is None:
        return None
    return next((e for e in protected.entries if _hit(base, e, recursive)), None)


def _mv_sources(flags: Sequence[str], targets: Sequence[str]) -> Sequence[str]:
    # With -t the position of the destination is unknown: every operand is checked (conservative).
    if any(f.startswith("--target-directory") or (not f.startswith("--") and "t" in f[1:]) for f in flags):
        return targets
    return targets[:-1]


def _operands(cmd: SimpleCommand) -> Tuple[Sequence[str], bool, str]:
    """(paths that would be removed or altered, whether ancestors count, operation label)."""
    flags, targets = split_args(cmd.args)
    if cmd.name == "rm":
        return targets, has_recursive(flags, "rR"), "removal"
    if cmd.name == "mv":
        return _mv_sources(flags, targets), True, "move"
    if cmd.name == "shred":
        return targets, False, "shred"
    if cmd.name in ("chmod", "chown", "chgrp") and has_recursive(flags, "R"):
        return targets, True, "recursive permission change"
    return (), False, ""


def check_protected(cmd: SimpleCommand, protected: Optional[ProtectedPaths]) -> Optional[RuleMatch]:
    if protected is None:
        return None
    operands, recursive, operation = _operands(cmd)
    for target in operands:
        entry = _protected_entry(target, recursive, protected)
        if entry is not None:
            return RuleMatch(RULE_ID, f"{operation} of a protected path ({entry}, from {ENV_VAR})")
    return None
