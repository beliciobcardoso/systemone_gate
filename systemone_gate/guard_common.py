"""Helpers shared by the deterministic guard rule modules."""

import posixpath
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

PROTECTED_BRANCHES = frozenset({"main", "master"})
BLOCK_DEV_PREFIX = r"/dev/(?:sd|nvme|hd|vd|xvd|mmcblk|disk|dm-|md|mapper/)"
# Data directories of database engines: anything below them is state that cannot be regenerated.
DB_DATA_DIR = re.compile(
    r"^/var/lib/(?:mysql|mariadb|postgresql|pgsql|mongodb|redis|clickhouse|etcd|kafka|cassandra|influxdb)(?:/|$)"
)
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"})

_HOME_FORMS = frozenset({"~", "$HOME", "${HOME}"})
_SYSTEM_DIRS = frozenset(
    {
        "/etc",
        "/usr",
        "/bin",
        "/sbin",
        "/lib",
        "/lib64",
        "/boot",
        "/var",
        "/home",
        "/root",
        "/dev",
        "/sys",
        "/proc",
        "/opt",
        "/srv",
    }
)


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    reason: str


def split_args(args: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Separate option-like tokens from positional targets (honours `--`)."""
    flags: List[str] = []
    targets: List[str] = []
    only_targets = False
    for arg in args:
        if only_targets or arg == "-" or not arg.startswith("-"):
            targets.append(arg)
        elif arg == "--":
            only_targets = True
        else:
            flags.append(arg)
    return flags, targets


def has_recursive(flags: Sequence[str], letters: str) -> bool:
    for flag in flags:
        if flag == "--recursive":
            return True
        if not flag.startswith("--") and any(c in flag[1:] for c in letters):
            return True
    return False


def resolve_literal_path(base: str) -> str:
    """Collapse `.`/`..`/repeated slashes in a literal absolute path.

    Paths with expansions (`$VAR`, backticks) are left untouched: they cannot
    be resolved statically.
    """
    if not base.startswith("/") or "$" in base or "`" in base:
        return base
    resolved = posixpath.normpath(base)
    return "/" + resolved.lstrip("/")  # POSIX keeps a leading `//` as is


def classify_target(target: str) -> Optional[str]:
    """'root' | 'home' | 'system' for catastrophic recursive targets."""
    base = target[:-2] if target.endswith("/*") else target
    if base:
        base = resolve_literal_path(base)
    base = base.rstrip("/")
    if base == "" and target.startswith("/"):
        return "root"
    if base in _HOME_FORMS:
        return "home"
    if base in _SYSTEM_DIRS:
        return "system"
    return None


def git_subcommand(args: Sequence[str]) -> Tuple[Optional[str], List[str]]:
    """Split `git [global options] <subcommand> <rest>`; global options that take a value are skipped."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        takes_value = args[i] in _GIT_VALUE_OPTIONS
        i += 2 if takes_value else 1
    if i >= len(args):
        return None, []
    return args[i], list(args[i + 1 :])
