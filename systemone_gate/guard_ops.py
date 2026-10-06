"""
Deterministic guard rules for infrastructure, cloud, database and git commands.

Same philosophy as `guard_rules`: only patterns that destroy shared or
production state without an interactive confirmation are blocked, and a
legitimate scoped variant (`kubectl delete pod`, `terraform plan`,
`aws s3 rm s3://bucket/tmp/ --recursive`) keeps working. Names that look like
production (`prod`, `production`, `prd`) are treated as a signal where the
command alone cannot tell a disposable target from a shared one.

Each tool declares the options that take a separate value, so a value such as
`-o name` or `--exclude '*.log'` is never mistaken for the verb or the target.
"""

import os
import re
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence

from .guard_common import (
    BLOCK_DEV_PREFIX,
    DB_DATA_DIR,
    PROTECTED_BRANCHES,
    RuleMatch,
    classify_target,
    git_subcommand,
    resolve_literal_path,
    split_args,
)
from .shell_parse import SimpleCommand

_PROD_NAME = re.compile(r"(?<![a-z0-9])(?:prod|production|prd)(?![a-z0-9])", re.I)
_S3_URL = re.compile(r"^s3://([^/]+)(.*)$", re.I)
_BLOCK_DEV = re.compile("^" + BLOCK_DEV_PREFIX)
_SENSITIVE_DIRS = (".ssh/", ".aws/", ".kube/", ".gnupg/")
_DESTRUCTIVE_EXEC = frozenset({"rm", "unlink", "shred"})
_FIND_GLOBAL_OPTIONS = frozenset({"-H", "-L", "-P", "-x"})
_LOG_FILE = re.compile(r"\.log(?:\.\d+)?$")

_KUBE_NAMESPACES = frozenset({"namespace", "namespaces", "ns"})
_KUBE_VOLUMES = frozenset(
    {"pvc", "persistentvolumeclaim", "persistentvolumeclaims", "pv", "persistentvolume", "persistentvolumes"}
)
_KUBECTL_VALUE_OPTIONS = frozenset(
    {
        "-n", "--namespace", "--context", "--cluster", "--kubeconfig", "-l", "--selector", "-f", "--filename",
        "--user", "--server", "-s", "-o", "--output", "--grace-period", "--timeout", "--cascade", "--field-selector",
        "--as", "--as-group", "--token", "--request-timeout", "--wait", "--now",
    }
)  # fmt: skip
_DOCKER_VALUE_OPTIONS = frozenset({"-H", "--host", "-c", "--context", "-l", "--log-level", "--config"})
_AWS_VALUE_OPTIONS = frozenset(
    {
        "--profile", "--region", "--endpoint-url", "--exclude", "--include", "--output", "--query", "--ca-bundle",
        "--cli-read-timeout", "--cli-connect-timeout", "--color",
    }
)  # fmt: skip
_CLOUD_VALUE_OPTIONS = frozenset(
    {
        "--project", "--region", "--zone", "--profile", "--subscription", "--name", "-n", "--resource-group", "-g",
        "--format", "--configuration", "--account", "--impersonate-service-account",
    }
)  # fmt: skip
_REDIS_VALUE_OPTIONS = frozenset(
    {
        "-h", "-p", "-a", "-n", "-u", "-s", "-r", "-i", "-x", "--user", "--pass", "--askpass", "--tls", "--sni",
        "--cacert", "--cert", "--key",
    }
)  # fmt: skip
_SAFE_LONG_OPTIONS = ("--dry-run", "--dryrun", "--help", "--version")


def _positionals(args: Sequence[str], value_options: FrozenSet[str] = frozenset()) -> List[str]:
    """Positional tokens, skipping options and the separate value of options known to take one."""
    result: List[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg.startswith("-"):
            skip = arg in value_options
        else:
            result.append(arg)
    return result


def _has_flag(args: Sequence[str], *names: str) -> bool:
    return any(a in names or a.split("=", 1)[0] in names for a in args)


def _short_flag_letters(args: Sequence[str]) -> str:
    return "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))


def _kinds(token: str) -> List[str]:
    """`pvc,pv` or `ns/prod` -> lowercase resource kinds."""
    return [part.split("/", 1)[0].lower() for part in token.split(",") if part]


def _check_kubectl(cmd: SimpleCommand) -> Optional[RuleMatch]:
    words = _positionals(cmd.args, _KUBECTL_VALUE_OPTIONS)
    if len(words) < 2 or words[0] != "delete":
        return None
    kinds = _kinds(words[1])
    names = words[1].split("/", 1)[1:] + words[2:]
    select_all = _has_flag(cmd.args, "--all")
    if any(k in _KUBE_NAMESPACES for k in kinds) and (select_all or any(_PROD_NAME.search(n) for n in names)):
        return RuleMatch("k8s-delete-namespace", "deleting a production namespace destroys every workload in it")
    scoped = bool(names) or _has_flag(cmd.args, "-l", "--selector")
    all_namespaces = _has_flag(cmd.args, "-A", "--all-namespaces") and not scoped
    if any(k in _KUBE_VOLUMES for k in kinds) and (select_all or all_namespaces):
        return RuleMatch("k8s-delete-all-volumes", "deleting every persistent volume claim destroys stored data")
    return None


def _check_terraform(cmd: SimpleCommand) -> Optional[RuleMatch]:
    words = _positionals(cmd.args)
    destroys = "destroy" in words[:2] or (words[:1] == ["apply"] and _has_flag(cmd.args, "-destroy"))
    if destroys and _has_flag(cmd.args, "-auto-approve", "--auto-approve", "--terragrunt-non-interactive"):
        return RuleMatch("terraform-destroy", "terraform destroy with -auto-approve removes all managed infrastructure")
    return None


def _s3_bucket_root(url: str) -> bool:
    """`s3://bucket`, `s3://bucket/`, `s3://bucket//` or `s3://bucket/.` (case-insensitive scheme)."""
    found = _S3_URL.match(url)
    return found is not None and not found.group(2).strip("/.")


def _check_aws(cmd: SimpleCommand) -> Optional[RuleMatch]:
    words = _positionals(cmd.args, _AWS_VALUE_OPTIONS)
    if len(words) < 3 or words[0] != "s3":
        return None
    if words[1] == "rb" and _has_flag(cmd.args, "--force"):
        return RuleMatch("aws-s3-remove-bucket", "aws s3 rb --force deletes a bucket and everything in it")
    if words[1] == "rm" and _has_flag(cmd.args, "--recursive") and _s3_bucket_root(words[2]):
        return RuleMatch("aws-s3-empty-bucket", "recursive aws s3 rm on a bucket root deletes every object")
    return None


def _check_gcloud(cmd: SimpleCommand) -> Optional[RuleMatch]:
    words = _positionals(cmd.args, _CLOUD_VALUE_OPTIONS)
    deletes = "delete" in words and words[0] in ("sql", "projects", "spanner", "bigtable")
    if deletes and _has_flag(cmd.args, "--quiet", "-q"):
        return RuleMatch("gcloud-delete-resource", "gcloud delete with --quiet removes a cloud resource unprompted")
    return None


def _check_az(cmd: SimpleCommand) -> Optional[RuleMatch]:
    words = _positionals(cmd.args, _CLOUD_VALUE_OPTIONS)
    deletes = "delete" in words and words[0] in ("group", "sql", "postgres", "mysql", "mariadb")
    if deletes and _has_flag(cmd.args, "--yes", "-y"):
        return RuleMatch("az-delete-resource", "az delete with --yes removes a cloud resource unprompted")
    return None


def _check_docker(cmd: SimpleCommand) -> Optional[RuleMatch]:
    words = _positionals(cmd.args, _DOCKER_VALUE_OPTIONS)
    if words[:2] == ["system", "prune"] and _has_flag(cmd.args, "--volumes"):
        return RuleMatch("docker-prune-volumes", "docker system prune --volumes deletes unused data volumes")
    forced = _has_flag(cmd.args, "--force") or "f" in _short_flag_letters(cmd.args)
    if words[:2] == ["volume", "prune"] and forced:
        return RuleMatch("docker-prune-volumes", "docker volume prune -f deletes unused data volumes")
    return None


def _check_crontab(cmd: SimpleCommand) -> Optional[RuleMatch]:
    letters = _short_flag_letters(cmd.args)
    if "r" in letters and "i" not in letters:
        return RuleMatch("crontab-remove", "crontab -r removes every scheduled job without confirmation")
    return None


def _check_userdel(cmd: SimpleCommand) -> Optional[RuleMatch]:
    if "r" in _short_flag_letters(cmd.args) or _has_flag(cmd.args, "--remove", "--remove-home"):
        return RuleMatch("user-remove-home", "removing a user together with their home directory deletes their files")
    return None


def _check_redis(cmd: SimpleCommand) -> Optional[RuleMatch]:
    words = _positionals(cmd.args, _REDIS_VALUE_OPTIONS)
    if words and words[0].upper() in ("FLUSHALL", "FLUSHDB"):
        return RuleMatch("redis-flush", "FLUSHALL/FLUSHDB deletes every key")
    return None


def _check_dropdb(cmd: SimpleCommand) -> Optional[RuleMatch]:
    if "i" in _short_flag_letters(cmd.args) or _has_flag(cmd.args, "--interactive"):
        return None
    return RuleMatch("database-drop", f"{cmd.name} deletes a whole database")


def _check_mysqladmin(cmd: SimpleCommand) -> Optional[RuleMatch]:
    forced = _has_flag(cmd.args, "--force") or "f" in _short_flag_letters(cmd.args)
    if "drop" in cmd.args and forced:
        return RuleMatch("database-drop", "mysqladmin drop --force deletes a whole database")
    return None


def _check_git_ops(cmd: SimpleCommand) -> Optional[RuleMatch]:
    sub, rest = git_subcommand(cmd.args)
    flags, targets = split_args(rest)
    letters = _short_flag_letters(flags)
    forced = _has_flag(flags, "--force") or "f" in letters
    if sub == "branch":
        deletes = "d" in letters or _has_flag(flags, "--delete")
        if ("D" in letters or (deletes and forced)) and any(t in PROTECTED_BRANCHES for t in targets):
            return RuleMatch("git-delete-protected-branch", "force-deleting main/master discards its history")
    if sub == "clean":
        dry_run = _has_flag(flags, "--dry-run") or "n" in letters
        if forced and ("x" in letters or "X" in letters) and not dry_run:
            return RuleMatch("git-clean-ignored", "git clean -x deletes ignored files, including local config")
    return None


def _executes_destructive(args: Sequence[str]) -> bool:
    if "-delete" in args:
        return True
    for idx, arg in enumerate(args):
        if arg in ("-exec", "-execdir", "-ok") and idx + 1 < len(args):
            if os.path.basename(args[idx + 1]) in _DESTRUCTIVE_EXEC:
                return True
    return False


def _check_find(cmd: SimpleCommand) -> Optional[RuleMatch]:
    args = list(cmd.args)
    start = 0
    while start < len(args) and (args[start] in _FIND_GLOBAL_OPTIONS or args[start].startswith(("-O", "-D"))):
        start += 1
    paths: List[str] = []
    for arg in args[start:]:
        if arg.startswith("-") or arg in ("(", "!"):
            break
        paths.append(arg)
    rooted = any(classify_target(p) in ("root", "system", "home") for p in paths)
    if rooted and _executes_destructive(args):
        return RuleMatch("find-delete-root", "find deleting files under /, $HOME or a system directory")
    return None


def _is_sensitive_target(target: str) -> bool:
    relative = target[2:] if target.startswith("~/") else target
    in_sensitive_dir = any(relative.startswith(d) or "/" + d in relative for d in _SENSITIVE_DIRS)
    return in_sensitive_dir or target.startswith("/etc/") or bool(_BLOCK_DEV.match(resolve_literal_path(target)))


def _check_shred(cmd: SimpleCommand) -> Optional[RuleMatch]:
    _, targets = split_args(cmd.args)
    if any(_is_sensitive_target(t) for t in targets):
        return RuleMatch("shred-sensitive", "shred irreversibly destroys keys, system config or a block device")
    return None


def _check_truncate(cmd: SimpleCommand) -> Optional[RuleMatch]:
    _, targets = split_args(cmd.args)
    for target in targets:
        path = resolve_literal_path(target)
        if DB_DATA_DIR.match(path) and not _LOG_FILE.search(path):
            return RuleMatch("truncate-database-file", "truncating a database data file corrupts the database")
    return None


def _check_mv(cmd: SimpleCommand) -> Optional[RuleMatch]:
    args = list(cmd.args)
    destinations = args[-1:]
    for idx, arg in enumerate(args):
        if arg in ("-t", "--target-directory") and idx + 1 < len(args):
            destinations.append(args[idx + 1])
        elif arg.startswith("--target-directory="):
            destinations.append(arg.split("=", 1)[1])
    if len(args) >= 2 and any(resolve_literal_path(d) == "/dev/null" for d in destinations):
        return RuleMatch("mv-to-devnull", "moving a path to /dev/null destroys it")
    return None


_HANDLERS: Dict[str, Callable[[SimpleCommand], Optional[RuleMatch]]] = {
    "kubectl": _check_kubectl,
    "terraform": _check_terraform,
    "tofu": _check_terraform,
    "terragrunt": _check_terraform,
    "aws": _check_aws,
    "gcloud": _check_gcloud,
    "az": _check_az,
    "docker": _check_docker,
    "crontab": _check_crontab,
    "userdel": _check_userdel,
    "deluser": _check_userdel,
    "redis-cli": _check_redis,
    "dropdb": _check_dropdb,
    "pg_dropcluster": _check_dropdb,
    "mysqladmin": _check_mysqladmin,
    "git": _check_git_ops,
    "find": _check_find,
    "shred": _check_shred,
    "truncate": _check_truncate,
    "mv": _check_mv,
}


def check_ops(cmd: SimpleCommand) -> Optional[RuleMatch]:
    """Match infrastructure/cloud/database/git commands that destroy shared state unprompted."""
    if any(a.startswith(_SAFE_LONG_OPTIONS) for a in cmd.args):  # dry runs and help text change nothing
        return None
    handler = _HANDLERS.get(cmd.name)
    return handler(cmd) if handler else None
