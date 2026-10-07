"""
Deterministic guard rules for shell commands.

Runs BEFORE (and instead of) the model: only unambiguously catastrophic
patterns are blocked, so legitimate scoped commands keep working. Quoted text
passed as plain data (e.g. `echo "rm -rf /"`) is never executed and never
blocks; text that the shell WOULD execute (`$(...)`, backticks, `sh -c`,
`eval`) is analysed recursively.

Known limits (by design): no variable expansion, no `xargs`, no scripts or
Makefiles invoked indirectly. This is a safety net, not a sandbox.
"""

import re
from typing import Optional, Sequence

from .guard_common import (
    BLOCK_DEV_PREFIX,
    DB_DATA_DIR,
    PROTECTED_BRANCHES,
    RuleMatch,
    classify_target,
    git_subcommand,
    has_recursive,
    resolve_literal_path,
    split_args,
)
from .guard_ops import check_ops
from .guard_protected import ProtectedPaths, check_protected
from .shell_parse import Segment, SimpleCommand, mask_quotes, normalize, scan, tokenize

MAX_DEPTH = 4

SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
SQL_CLIENTS = frozenset({"psql", "mysql", "mariadb", "sqlite3", "sqlcmd", "clickhouse-client"})

_CONFIG_PARENTS = frozenset({"etc", "boot"})
_SYSTEM_LIB_DIRS = frozenset({"/usr/bin", "/usr/sbin", "/usr/lib", "/usr/lib64"})
_REGENERABLE_VAR_LIB = frozenset({"apt", "dpkg", "cloud"})
_REPO_GIT_DIRS = (".git", "./.git", "../.git")
_BLOCK_DEV = BLOCK_DEV_PREFIX

_FORK_BOMB = re.compile(r"([:\w]{1,32})\s*\(\s*\)\s*\{\s*\1\s*\|\s*\1\s*&\s*\}\s*;\s*\1")
_REDIRECT_DEV = re.compile(r">>?\s*" + _BLOCK_DEV)
_DD_OF = re.compile(r"^of=" + _BLOCK_DEV)
_SQL_DROP = re.compile(r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b", re.I)
_SQL_TRUNCATE_STRICT = re.compile(r"\bTRUNCATE\s+TABLE\b", re.I)
_SQL_TRUNCATE_LOOSE = re.compile(r"\bTRUNCATE\s+(?:TABLE\s+)?[A-Za-z_\"`\[]", re.I)
_SQL_DELETE = re.compile(r"\bDELETE\s+FROM\s+\S+(?P<rest>.*)", re.I | re.S)
_SQL_WHERE = re.compile(r"\bWHERE\b", re.I)
_SQL_LEADING = frozenset({"DROP", "TRUNCATE", "DELETE"})
_DOWNLOAD_SUBST = re.compile(r"^\s*(?:\$\(|`|<\()\s*(?:curl|wget)\b")


def _check_rm(cmd: SimpleCommand) -> Optional[RuleMatch]:
    flags, targets = split_args(cmd.args)
    if "--no-preserve-root" in flags:
        return RuleMatch("rm-recursive-root", "rm with --no-preserve-root removes the root filesystem")
    if not has_recursive(flags, "rR"):
        return None
    kinds = {classify_target(t) for t in targets}
    if "root" in kinds:
        return RuleMatch("rm-recursive-root", "recursive removal of the filesystem root")
    if "home" in kinds:
        return RuleMatch("rm-recursive-home", "recursive removal of the entire home directory")
    if "system" in kinds:
        return RuleMatch("rm-recursive-system-dir", "recursive removal of a system directory")
    return _check_rm_service_or_git(targets)


def _is_service_dir(target: str) -> bool:
    """Config or service state: `/etc/<x>`, `/boot/<x>`, `/usr/{bin,lib}`, `/var/lib[/<x>]`, DB data dirs."""
    if not target.startswith("/"):
        return False
    base = target[:-2] if target.endswith("/*") else target
    path = resolve_literal_path(base).rstrip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) == 2 and parts[0] in _CONFIG_PARENTS:
        return True
    if path in _SYSTEM_LIB_DIRS or DB_DATA_DIR.match(path + "/"):
        return True
    if parts[:2] == ["var", "lib"]:
        return len(parts) == 2 or (len(parts) == 3 and parts[2] not in _REGENERABLE_VAR_LIB)
    return False


def _is_repo_git_dir(target: str) -> bool:
    """The repository's own `.git` (relative to the working directory), or a path inside it."""
    base = target[:-2] if target.endswith("/*") else target
    base = base.rstrip("/")
    return base in _REPO_GIT_DIRS or base.startswith((".git/", "./.git/"))


def _check_rm_service_or_git(targets: Sequence[str]) -> Optional[RuleMatch]:
    for target in targets:
        if _is_repo_git_dir(target):
            return RuleMatch("rm-recursive-git-dir", "recursive removal of the repository .git destroys its history")
        if _is_service_dir(target):
            return RuleMatch("rm-recursive-service-dir", "recursive removal of a system config or service directory")
    return None


def _check_dd(cmd: SimpleCommand) -> Optional[RuleMatch]:
    if any(_DD_OF.match(a) for a in cmd.args):
        return RuleMatch("dd-block-device", "dd writing directly to a block device destroys the disk")
    return None


def _check_mkfs(cmd: SimpleCommand) -> Optional[RuleMatch]:
    if any(a.startswith("/dev/") for a in cmd.args):
        return RuleMatch("mkfs-device", "formatting a device erases all of its data")
    return None


def _check_chmod(cmd: SimpleCommand) -> Optional[RuleMatch]:
    flags, targets = split_args(cmd.args)
    if not has_recursive(flags, "R"):
        return None
    if any(classify_target(t) in ("root", "system") for t in targets):
        return RuleMatch("chmod-recursive-root", "recursive chmod/chown on the root or a system directory")
    return None


def _push_ref(target: str) -> str:
    ref = target.lstrip("+")
    ref = ref.split(":", 1)[1] if ":" in ref else ref
    return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref


def _check_git(cmd: SimpleCommand) -> Optional[RuleMatch]:
    sub, rest = git_subcommand(cmd.args)
    if sub != "push":
        return None
    flags, targets = split_args(rest)
    forced = "--force" in flags or any(
        not f.startswith("--") and "f" in f[1:] for f in flags
    )
    for target in targets:
        if _push_ref(target) in PROTECTED_BRANCHES and (forced or target.startswith("+")):
            return RuleMatch(
                "git-force-push-protected",
                "force push to main/master rewrites shared history",
            )
    return None


def _script_of_shell(args: Sequence[str]) -> Optional[str]:
    for idx, arg in enumerate(args):
        if arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]:
            return args[idx + 1] if idx + 1 < len(args) else None
    return None


def _check_shell_download(cmd: SimpleCommand) -> Optional[RuleMatch]:
    if any(_DOWNLOAD_SUBST.match(a) or a.startswith("<(curl") or a.startswith("<(wget") for a in cmd.args):
        return _download_match()
    return None


def _download_match() -> RuleMatch:
    return RuleMatch("download-pipe-shell", "running a script downloaded from the network without inspection")


def _sql_match(text: str, loose_truncate: bool) -> Optional[RuleMatch]:
    truncate = _SQL_TRUNCATE_LOOSE if loose_truncate else _SQL_TRUNCATE_STRICT
    for statement in text.split(";"):
        if _SQL_DROP.search(statement):
            return RuleMatch("sql-drop", "DROP of a table/database/schema is irreversible")
        if truncate.search(statement):
            return RuleMatch("sql-truncate", "TRUNCATE removes every row of the table")
        found = _SQL_DELETE.search(statement)
        if found and not _SQL_WHERE.search(found.group("rest")):
            return RuleMatch("sql-delete-no-where", "DELETE without WHERE removes every row of the table")
    return None


def _check_sql(seg: Segment, cmd: SimpleCommand, nxt: Optional[SimpleCommand]) -> Optional[RuleMatch]:
    piped_to_client = seg.sep_after == "|" and nxt is not None and nxt.name in SQL_CLIENTS
    if cmd.name in SQL_CLIENTS or piped_to_client:
        return _sql_match(seg.text, loose_truncate=True)
    if cmd.name.upper() in _SQL_LEADING:
        return _sql_match(" ".join((cmd.name,) + cmd.args), loose_truncate=False)
    return None


def _check_pipe_download(seg: Segment, cmd: SimpleCommand, nxt: Optional[SimpleCommand]) -> Optional[RuleMatch]:
    if cmd.name in ("curl", "wget") and seg.sep_after == "|" and nxt and nxt.name in SHELLS:
        return _download_match()
    return None


def _check_nested(cmd: SimpleCommand, depth: int, protected: Optional[ProtectedPaths]) -> Optional[RuleMatch]:
    if cmd.name == "eval":
        return _evaluate(" ".join(cmd.args), depth + 1, protected)
    if cmd.name not in SHELLS:
        return None
    script = _script_of_shell(cmd.args)
    if script is not None:
        if _DOWNLOAD_SUBST.match(script):
            return _download_match()
        return _evaluate(script, depth + 1, protected)
    return _check_shell_download(cmd)


def _check_command(cmd: SimpleCommand) -> Optional[RuleMatch]:
    if cmd.name == "rm":
        return _check_rm(cmd)
    if cmd.name == "dd":
        return _check_dd(cmd)
    if cmd.name.startswith("mkfs") or cmd.name == "mkswap":
        return _check_mkfs(cmd)
    if cmd.name in ("chmod", "chown", "chgrp"):
        return _check_chmod(cmd)
    if cmd.name == "git":
        return _check_git(cmd) or check_ops(cmd)
    return check_ops(cmd)


def _check_segment(
    seg: Segment, cmd: SimpleCommand, nxt: Optional[SimpleCommand], depth: int, protected: Optional[ProtectedPaths]
) -> Optional[RuleMatch]:
    if _REDIRECT_DEV.search(mask_quotes(seg.text)):
        return RuleMatch("redirect-block-device", "redirecting to a block device destroys the disk")
    return (
        _check_command(cmd)
        or check_protected(cmd, protected)
        or _check_sql(seg, cmd, nxt)
        or _check_pipe_download(seg, cmd, nxt)
        or _check_nested(cmd, depth, protected)
    )


def _evaluate(text: str, depth: int, protected: Optional[ProtectedPaths]) -> Optional[RuleMatch]:
    if depth > MAX_DEPTH:
        return None
    if _FORK_BOMB.search(mask_quotes(text)):
        return RuleMatch("fork-bomb", "fork bomb exhausts the machine resources")
    segments, subs = scan(text)
    commands = [normalize(tokenize(seg.text)) for seg in segments]
    for idx, seg in enumerate(segments):
        nxt = commands[idx + 1] if idx + 1 < len(commands) else None
        match = _check_segment(seg, commands[idx], nxt, depth, protected)
        if match:
            return match
    for sub in subs:
        match = _evaluate(sub, depth + 1, protected)
        if match:
            return match
    return None


def evaluate_command(command: str, *, protected: Optional[ProtectedPaths] = None) -> Optional[RuleMatch]:
    """Return the first catastrophic-pattern match for `command`, else None.

    `protected` adds the user's protected paths (see `guard_protected`) to the built-in rules.
    """
    if not isinstance(command, str):
        return None
    return _evaluate(command, 0, protected)
