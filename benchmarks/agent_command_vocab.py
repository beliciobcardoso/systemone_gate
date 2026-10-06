"""Allow-list vocabulary for real agent commands that may be published in the held-out set.

A command taken from a session log is kept only if EVERY word in it (tool, subcommand, flag, path segment,
branch or file name part) is generic developer vocabulary. A customer, project, host, branch or person name
is, by construction, not in this list, so it cannot slip through the way it would with a block list.

The list holds generic words only. Never add a name that identifies a person, company, customer, host or
project, and never add a word just to make one particular command pass.
"""

import re
from typing import FrozenSet

TOOLS: FrozenSet[str] = frozenset(
    """
    git npm npx pnpm yarn node python python3 pip pip3 pytest ruff mypy black isort make cargo go rustc java mvn
    gradle ls cat head tail grep rg find wc sort uniq echo printf cd pwd mkdir rmdir rm cp mv touch chmod chown
    tree diff sed awk cut tr xargs docker compose kubectl terraform tofu aws gcloud az helm redis cli psql
    sqlite3 tar zip unzip gzip which whoami date env export source bash sh true false test read clear
    """.split()
)

WORDS: FrozenSet[str] = frozenset(
    """
    status log diff add commit push pull fetch checkout switch branch merge rebase stash reset restore show tag
    clean remote clone init config blame describe cherry pick apply install ci run build lint format start dev
    publish version help list ps up down logs exec images image prune volume rmi stop kill inspect get create
    plan destroy validate fmt output state check fix watch coverage cov delete rollout restart scale top
    namespace namespaces ns pod pods pvc deployment deployments service services node nodes
    recursive force verbose quiet silent all dry no verify stat short oneline graph name only cached staged
    amend hard soft mixed global local write json yaml lease volumes filter limit max count depth auto approve
    yes interactive follow tail since until
    src lib app apps test tests spec specs docs doc dist build out target bin tmp temp cache modules vendor
    public static assets scripts script config configs conf env example examples sample data log backup backups
    migrations migration db models model views controllers routes components pages utils helpers types hooks
    styles css html js ts tsx jsx toml md txt lock sh py rs java kt xml csv sql ini cfg pyc pycache github
    gitignore dockerfile makefile readme license changelog package main index setup pyproject requirements venv
    etc usr var opt home user root srv dev null proc sys mnt nginx postgres postgresql mysql redis mongo
    prod production staging stage development feature feat hotfix release bugfix master origin head upstream
    new old copy bak orig unit integration e2e report reports results input file files dir directory folder
    bench benchmarks examples tool tools core common shared base typo wip docs refactor chore perf ci
    node_modules  a b c x y z foo bar baz
    """.split()
)

_ATOM_SPLIT = re.compile(r"[^a-z0-9]+")
_NUMBER = re.compile(r"\d{1,6}")
_REDIRECT = re.compile(r"^\d*(?:>>?|<)&?\d*$")
_GLOB_CHARS = re.compile(r"[*?\[\]{}~]")
MAX_TOKEN_CHARS = 48


def atoms(token: str) -> list:
    """Lowercase alphanumeric parts of a token, with glob characters and separators removed."""
    cleaned = _GLOB_CHARS.sub(" ", token.lower())
    return [a for a in _ATOM_SPLIT.split(cleaned) if a]


def token_is_generic(token: str, *, first: bool) -> bool:
    """True when every part of ``token`` is generic vocabulary (``first``: the token is the command name)."""
    if len(token) > MAX_TOKEN_CHARS:
        return False
    if _REDIRECT.match(token):
        return True
    allowed = TOOLS if first else TOOLS | WORDS
    return all(a in allowed or _NUMBER.fullmatch(a) for a in atoms(token))
