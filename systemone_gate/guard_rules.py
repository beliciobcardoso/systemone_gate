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

import posixpath
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .shell_parse import Segment, SimpleCommand, mask_quotes, normalize, scan, tokenize

MAX_DEPTH = 4

SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
SQL_CLIENTS = frozenset({"psql", "mysql", "mariadb", "sqlite3", "sqlcmd", "clickhouse-client"})

_HOME_FORMS = frozenset({"~", "$HOME", "${HOME}"})
_SYSTEM_DIRS = frozenset({
    "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/var",
    "/home", "/root", "/dev", "/sys", "/proc", "/opt", "/srv",
})
_PROTECTED_BRANCHES = frozenset({"main", "master"})
_BLOCK_DEV = r"/dev/(?:sd|nvme|hd|vd|mmcblk|disk)"

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


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    reason: str


def _split_args(args: Sequence[str]) -> Tuple[List[str], List[str]]:
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


def _has_recursive(flags: Sequence[str], letters: str) -> bool:
    for flag in flags:
        if flag == "--recursive":
            return True
        if not flag.startswith("--") and any(c in flag[1:] for c in letters):
            return True
    return False


def _resolve_literal_path(base: str) -> str:
    """Collapse `.`/`..`/repeated slashes in a literal absolute path.

    Paths with expansions (`$VAR`, backticks) are left untouched: they cannot
    be resolved statically.
    """
    if not base.startswith("/") or "$" in base or "`" in base:
        return base
    resolved = posixpath.normpath(base)
    return "/" + resolved.lstrip("/")  # POSIX keeps a leading `//` as is


def _classify_target(target: str) -> Optional[str]:
    """'root' | 'home' | 'system' for catastrophic recursive targets."""
    base = target[:-2] if target.endswith("/*") else target
    if base:
        base = _resolve_literal_path(base)
    base = base.rstrip("/")
    if base == "" and target.startswith("/"):
        return "root"
    if base in _HOME_FORMS:
        return "home"
    if base in _SYSTEM_DIRS:
        return "system"
    return None


def _check_rm(cmd: SimpleCommand) -> Optional[RuleMatch]:
    flags, targets = _split_args(cmd.args)
    if "--no-preserve-root" in flags:
        return RuleMatch("rm-recursive-root", "rm com --no-preserve-root remove o sistema de arquivos raiz")
    if not _has_recursive(flags, "rR"):
        return None
    kinds = {_classify_target(t) for t in targets}
    if "root" in kinds:
        return RuleMatch("rm-recursive-root", "remoção recursiva da raiz do sistema de arquivos")
    if "home" in kinds:
        return RuleMatch("rm-recursive-home", "remoção recursiva do diretório home inteiro")
    if "system" in kinds:
        return RuleMatch("rm-recursive-system-dir", "remoção recursiva de diretório de sistema")
    return None


def _check_dd(cmd: SimpleCommand) -> Optional[RuleMatch]:
    if any(_DD_OF.match(a) for a in cmd.args):
        return RuleMatch("dd-block-device", "dd gravando direto em dispositivo de bloco destrói o disco")
    return None


def _check_mkfs(cmd: SimpleCommand) -> Optional[RuleMatch]:
    if any(a.startswith("/dev/") for a in cmd.args):
        return RuleMatch("mkfs-device", "formatação de dispositivo apaga todos os dados dele")
    return None


def _check_chmod(cmd: SimpleCommand) -> Optional[RuleMatch]:
    flags, targets = _split_args(cmd.args)
    if not _has_recursive(flags, "R"):
        return None
    if any(_classify_target(t) in ("root", "system") for t in targets):
        return RuleMatch("chmod-recursive-root", "chmod/chown recursivo na raiz ou em diretório de sistema")
    return None


def _push_ref(target: str) -> str:
    ref = target.lstrip("+")
    ref = ref.split(":", 1)[1] if ":" in ref else ref
    return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref


def _check_git(cmd: SimpleCommand) -> Optional[RuleMatch]:
    args, i = list(cmd.args), 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in ("-C", "-c") else 1
    if i >= len(args) or args[i] != "push":
        return None
    flags, targets = _split_args(args[i + 1:])
    forced = "--force" in flags or any(
        not f.startswith("--") and "f" in f[1:] for f in flags
    )
    for target in targets:
        if _push_ref(target) in _PROTECTED_BRANCHES and (forced or target.startswith("+")):
            return RuleMatch("git-force-push-protected", "force push em main/master reescreve o histórico compartilhado")
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
    return RuleMatch("download-pipe-shell", "execução de script baixado da rede sem inspeção")


def _sql_match(text: str, loose_truncate: bool) -> Optional[RuleMatch]:
    truncate = _SQL_TRUNCATE_LOOSE if loose_truncate else _SQL_TRUNCATE_STRICT
    for statement in text.split(";"):
        if _SQL_DROP.search(statement):
            return RuleMatch("sql-drop", "DROP de tabela/banco/schema é irreversível")
        if truncate.search(statement):
            return RuleMatch("sql-truncate", "TRUNCATE apaga todas as linhas da tabela")
        found = _SQL_DELETE.search(statement)
        if found and not _SQL_WHERE.search(found.group("rest")):
            return RuleMatch("sql-delete-no-where", "DELETE sem WHERE apaga todas as linhas da tabela")
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


def _check_nested(cmd: SimpleCommand, depth: int) -> Optional[RuleMatch]:
    if cmd.name == "eval":
        return _evaluate(" ".join(cmd.args), depth + 1)
    if cmd.name not in SHELLS:
        return None
    script = _script_of_shell(cmd.args)
    if script is not None:
        if _DOWNLOAD_SUBST.match(script):
            return _download_match()
        return _evaluate(script, depth + 1)
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
        return _check_git(cmd)
    return None


def _check_segment(
    seg: Segment, cmd: SimpleCommand, nxt: Optional[SimpleCommand], depth: int
) -> Optional[RuleMatch]:
    if _REDIRECT_DEV.search(mask_quotes(seg.text)):
        return RuleMatch("redirect-block-device", "redirecionamento para dispositivo de bloco destrói o disco")
    return (
        _check_command(cmd)
        or _check_sql(seg, cmd, nxt)
        or _check_pipe_download(seg, cmd, nxt)
        or _check_nested(cmd, depth)
    )


def _evaluate(text: str, depth: int) -> Optional[RuleMatch]:
    if depth > MAX_DEPTH:
        return None
    if _FORK_BOMB.search(mask_quotes(text)):
        return RuleMatch("fork-bomb", "fork bomb esgota os recursos da máquina")
    segments, subs = scan(text)
    commands = [normalize(tokenize(seg.text)) for seg in segments]
    for idx, seg in enumerate(segments):
        nxt = commands[idx + 1] if idx + 1 < len(commands) else None
        match = _check_segment(seg, commands[idx], nxt, depth)
        if match:
            return match
    for sub in subs:
        match = _evaluate(sub, depth + 1)
        if match:
            return match
    return None


def evaluate_command(command: str) -> Optional[RuleMatch]:
    """Return the first catastrophic-pattern match for `command`, else None."""
    if not isinstance(command, str):
        return None
    return _evaluate(command, 0)
