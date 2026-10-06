"""Schema and loader for the threshold-calibration datasets (stdlib + systemone_gate only).

A dataset file is a JSON object::

    {"schema_version": 1, "surface": "guard" | "diff", "cases": [ ... ]}

The decision being calibrated is binary: ``should_block``. The rubric labels used by
``rubric_language.py`` (risk level, breaking change) are not stored here.

Usage: python benchmarks/calibration_schema.py [DIR] [--require-final]
(validates and summarizes; default data/calibration; --require-final also rejects unreviewed labels)
Exit code is 1 when any file is invalid.
"""

import hashlib
import json
import os
import re
import sys
from collections import Counter
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from systemone_gate.guard_rules import evaluate_command  # noqa: E402
from systemone_gate.redact import redact_secrets  # noqa: E402

SCHEMA_VERSION = 1
DEFAULT_DATA_DIR = os.path.join(HERE, "data", "calibration")

SPLIT_HELDOUT = "heldout"
# Files whose content decides what the deterministic rules block: a change to any of them after a held-out
# set was frozen means the set no longer measures the rules out of sample.
RULES_FILES = ("guard_common.py", "guard_ops.py", "guard_rules.py", "shell_parse.py")
COMMAND_SOURCES = frozenset({"synthetic", "generated", "agent_log"})
FREEZE_KEYS = ("rules_sha256_16", "frozen_on")
_FREEZE_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_FREEZE_HASH_RE = re.compile(r"[0-9a-f]{16}")

SURFACE_GUARD = "guard"
SURFACE_DIFF = "diff"
SURFACES = (SURFACE_GUARD, SURFACE_DIFF)

# Conservative proxy for the endpoint's ~2050-token input limit (~3 chars per token); the real
# token count is only known at collection time.
MAX_STATE_CHARS = 6000

LABEL_SOURCES = {
    SURFACE_GUARD: frozenset({"synthetic", "man_page", "blind_labeler"}),
    SURFACE_DIFF: frozenset({"synthetic", "revert", "hotfix", "changelog_breaking", "negative_baseline"}),
}
# Sources whose label comes from the history of a real project and therefore need attribution.
OUTCOME_SOURCES = frozenset({"revert", "hotfix", "changelog_breaking", "negative_baseline"})

# The repository is public: only permissive licenses may be redistributed as test data.
PERMISSIVE_LICENSES = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"})

STATUS_UNREVIEWED = "unreviewed"
STATUS_AGREED = "agreed"
STATUS_DISPUTED = "disputed"
STATUS_RESOLVED = "resolved"
REVIEW_STATUSES = (STATUS_UNREVIEWED, STATUS_AGREED, STATUS_DISPUTED, STATUS_RESOLVED)
FINAL_STATUSES = frozenset({STATUS_AGREED, STATUS_RESOLVED})

_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,63}")
_HANDLE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,38}")
_REPO_SEGMENT = r"[A-Za-z0-9_-][A-Za-z0-9_.-]*"
_REPO_RE = re.compile(_REPO_SEGMENT + "/" + _REPO_SEGMENT)
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_DIFF_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.M)
_DIFF_NEW_FILE_RE = re.compile(r"^\+\+\+ ", re.M)

# The redactor is best-effort and misses common shapes; these extra patterns cover the ones that
# matter for a public dataset. Matching is deliberately aggressive: a false positive only means
# rewording a case, a false negative publishes a credential or personal data.
_EXTRA_SENSITIVE = (
    ("basic-auth", re.compile(r"Authorization:\s*Basic\s+\S+", re.I)),
    ("cli-password-flag", re.compile(r"\b(?:mysql|mariadb|psql|mongo|docker\s+login)\b[^\n]*\s-p\s*\S{4,}", re.I)),
    ("password-assignment", re.compile(r"\b(?:password|passwd|pwd|secret|token)\s*[=:]\s*\S{4,}", re.I)),
    (
        "api-key-prefix",
        re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|glpat-[A-Za-z0-9_-]{16,}|xox[baprs]-[A-Za-z0-9-]{10,})"),
    ),
    (
        "private-ip",
        re.compile(r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
    ),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+")),
    ("user-home", re.compile(r"/home/(?!user\b)[a-z_][a-z0-9_-]*", re.I)),
)

_REQUIRED = ("id", "state", "should_block", "label_source", "label_evidence", "review_status")
_OPTIONAL = (
    "second_label",
    "second_labeler",
    "second_rationale",
    "primary_label",
    "resolved_by",
    "tags",
    "provenance",
    "language",
    "rules_catch",
    "command_source",
    "generated_by",
)
_PROVENANCE_KEYS = ("repo", "commit", "license", "url")
MAX_RATIONALE_CHARS = 500


class CalibrationDataError(ValueError):
    """A dataset file is unreadable or has invalid content; the message lists every problem."""


def rules_fingerprint() -> str:
    """Short hash of the code that decides what the deterministic rules block (see RULES_FILES)."""
    digest = hashlib.sha256()
    package = os.path.join(os.path.dirname(HERE), "systemone_gate")
    for name in RULES_FILES:
        with open(os.path.join(package, name), "rb") as handle:
            digest.update(name.encode("utf-8") + b"\0" + handle.read() + b"\0")
    return digest.hexdigest()[:16]


def sensitive_rules(text: str) -> List[str]:
    """Names of the secret/personal-data patterns found in ``text`` (empty when clean)."""
    found = list(redact_secrets(text).findings) + [rule for rule, rx in _EXTRA_SENSITIVE if rx.search(text)]
    return sorted(set(found))


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_provenance(prov: Any) -> List[str]:
    if not isinstance(prov, dict):
        return ["provenance deve ser um objeto"]
    errors = [f"provenance.{key} desconhecido" for key in prov if key not in _PROVENANCE_KEYS]
    errors.extend(f"provenance.{key} ausente ou vazio" for key in _PROVENANCE_KEYS if not _is_text(prov.get(key)))
    if errors:
        return errors
    repo, commit, url = prov["repo"], prov["commit"], prov["url"]
    if not _REPO_RE.fullmatch(repo) or any(part in (".", "..") for part in repo.split("/")):
        errors.append("provenance.repo deve ter o formato owner/name")
    if not _SHA_RE.fullmatch(commit):
        errors.append("provenance.commit deve ser um SHA completo de 40 hex minúsculos")
    if prov["license"] not in PERMISSIVE_LICENSES:
        errors.append(f"provenance.license deve ser uma de {sorted(PERMISSIVE_LICENSES)}")
    if not (url.startswith(f"https://github.com/{repo}/") and commit in url):
        errors.append("provenance.url deve apontar para o commit em https://github.com/<repo>/")
    return errors


def _check_review(case: Mapping[str, Any]) -> List[str]:
    status = case.get("review_status")
    if status not in REVIEW_STATUSES:
        return [f"review_status deve ser um de {list(REVIEW_STATUSES)}"]
    second = case.get("second_label")
    resolver = case.get("resolved_by")
    primary = case.get("should_block")
    errors: List[str] = []
    if second is not None and not isinstance(second, bool):
        return ["second_label deve ser bool ou null"]
    if resolver is not None and not (isinstance(resolver, str) and _HANDLE_RE.fullmatch(resolver)):
        errors.append("resolved_by deve ser um handle ^[a-z0-9][a-z0-9_-]{1,38}$ ou null (sem e-mail ou nome)")
    labeler = case.get("second_labeler")
    if labeler is not None and not (isinstance(labeler, str) and _HANDLE_RE.fullmatch(labeler)):
        errors.append("second_labeler deve ser um handle ^[a-z0-9][a-z0-9_-]{1,38}$ ou null")
    if second is not None and labeler is None:
        errors.append("second_label exige second_labeler (quem rotulou, para a proveniência do rótulo)")
    if second is None and labeler is not None:
        errors.append("second_labeler só é permitido junto com second_label")
    rationale = case.get("second_rationale")
    if rationale is not None:
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append("second_rationale deve ser texto não vazio ou null")
        elif len(rationale) > MAX_RATIONALE_CHARS:
            errors.append(f"second_rationale excede {MAX_RATIONALE_CHARS} caracteres")
        if second is None:
            errors.append("second_rationale só é permitido junto com second_label")
    original = case.get("primary_label")
    if original is not None and not isinstance(original, bool):
        errors.append("primary_label deve ser bool ou null")
    if status == STATUS_RESOLVED and not isinstance(original, bool):
        errors.append("review_status 'resolved' exige primary_label (o rótulo original do primário, para auditoria)")
    if status != STATUS_RESOLVED and original is not None:
        errors.append("primary_label só é permitido com review_status 'resolved'")
    if status != STATUS_RESOLVED and resolver is not None:
        errors.append("resolved_by só é permitido com review_status 'resolved'")
    if status == STATUS_UNREVIEWED and second is not None:
        errors.append("second_label deve ser null com review_status 'unreviewed'")
    if status == STATUS_AGREED and (second is None or second != primary):
        errors.append("review_status 'agreed' exige second_label igual a should_block")
    if status == STATUS_DISPUTED and (second is None or second == primary):
        errors.append("review_status 'disputed' exige second_label diferente de should_block")
    if status == STATUS_RESOLVED:
        if second is None:
            errors.append("review_status 'resolved' exige second_label")
        if not _is_text(resolver):
            errors.append("review_status 'resolved' exige resolved_by")
    return errors


def _check_guard(case: Mapping[str, Any], heldout: bool = False, rules_unchanged: bool = True) -> List[str]:
    """``heldout``: a safe-labeled command that a rule blocks is a measured false positive of the rules, not a
    data error. ``rules_unchanged``: false once the rules differ from the freeze, so the stored ``rules_catch``
    (computed at freeze time) can no longer be compared with the current rules."""
    if "provenance" in case:
        return ["provenance só existe na superfície diff"]
    if not isinstance(case.get("rules_catch"), bool):
        return ["rules_catch ausente ou não bool (é derivado de guard_rules)"]
    errors: List[str] = []
    if rules_unchanged:
        matched = evaluate_command(str(case["state"])) is not None
        if case["rules_catch"] != matched:
            errors.append(f"rules_catch={case['rules_catch']} diverge de guard_rules (calculado: {matched})")
    if not heldout and case["rules_catch"] and case.get("should_block") is False:
        errors.append("uma regra determinística bloqueia este comando, mas o rótulo diz que é seguro")
    return errors


def _check_command_source(case: Mapping[str, Any]) -> List[str]:
    source = case.get("command_source")
    generator = case.get("generated_by")
    errors: List[str] = []
    if source is not None and source not in COMMAND_SOURCES:
        errors.append(f"command_source deve ser um de {sorted(COMMAND_SOURCES)}")
    if generator is not None and not (isinstance(generator, str) and _HANDLE_RE.fullmatch(generator)):
        errors.append("generated_by deve ser um handle ^[a-z0-9][a-z0-9_-]{1,38}$")
    if source == "generated" and generator is None:
        errors.append("command_source 'generated' exige generated_by")
    if source != "generated" and generator is not None:
        errors.append("generated_by só é permitido com command_source 'generated'")
    return errors


def _check_diff(case: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    state = str(case["state"])
    if "rules_catch" in case:
        errors.append("rules_catch só existe na superfície guard")
    if not (_DIFF_HUNK_RE.search(state) and _DIFF_NEW_FILE_RE.search(state)):
        errors.append("state não parece um diff unificado (faltam '+++' e um cabeçalho '@@ -a,b +c,d @@')")
    if case.get("label_source") in OUTCOME_SOURCES:
        if "provenance" not in case:
            errors.append("provenance é obrigatório para rótulo derivado do histórico de um projeto")
        else:
            errors.extend(_check_provenance(case["provenance"]))
    elif "provenance" in case:
        errors.extend(_check_provenance(case["provenance"]))
    return errors


def validate_case(
    case: Mapping[str, Any], surface: str, heldout: bool = False, rules_unchanged: bool = True
) -> List[str]:
    """Returns every problem found in one case (empty list when valid). Does not raise on bad data."""
    if surface not in SURFACES:
        raise ValueError(f"surface desconhecida: {surface!r}")
    if not isinstance(case, dict):
        return ["caso deve ser um objeto"]
    errors = [f"campo obrigatório ausente: {key}" for key in _REQUIRED if key not in case]
    errors.extend(f"campo desconhecido: {key}" for key in case if key not in _REQUIRED + _OPTIONAL)
    if errors:
        return errors
    if not isinstance(case["id"], str) or not _ID_RE.fullmatch(case["id"]):
        errors.append("id deve casar [a-z0-9][a-z0-9_-]{1,63}")
    if not _is_text(case["state"]):
        errors.append("state deve ser texto não vazio")
    elif len(case["state"]) > MAX_STATE_CHARS:
        errors.append(f"state excede {MAX_STATE_CHARS} caracteres")
    if not isinstance(case["should_block"], bool):
        errors.append("should_block deve ser bool")
    if not (isinstance(case["label_source"], str) and case["label_source"] in LABEL_SOURCES[surface]):
        errors.append(f"label_source deve ser um de {sorted(LABEL_SOURCES[surface])} na superfície {surface}")
    if not _is_text(case["label_evidence"]):
        errors.append("label_evidence deve ser texto não vazio")
    tags = case.get("tags")
    if tags is not None and not (isinstance(tags, list) and all(_is_text(t) for t in tags)):
        errors.append("tags deve ser uma lista de textos não vazios")
    errors.extend(_check_review(case))
    errors.extend(_check_command_source(case))
    errors.extend(_sensitive_errors(case))
    if errors:
        return errors
    if surface == SURFACE_GUARD:
        return errors + _check_guard(case, heldout=heldout, rules_unchanged=rules_unchanged)
    return errors + _check_diff(case)


def _string_fields(case: Mapping[str, Any]) -> Iterator[Tuple[str, str]]:
    """Every free-text value of a case, with its field name, for the public-repo secret scan."""
    for key in ("state", "label_evidence", "language", "second_rationale"):
        if isinstance(case.get(key), str):
            yield key, case[key]
    for tag in case.get("tags") or []:
        if isinstance(tag, str):
            yield "tags", tag
    prov = case.get("provenance")
    if isinstance(prov, dict):
        for key, value in prov.items():
            if isinstance(value, str):
                yield f"provenance.{key}", value


def _sensitive_errors(case: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    for field, text in _string_fields(case):
        found = sensitive_rules(text)
        if found:
            errors.append(f"{field} contém padrão de segredo ou dado pessoal {found} (repositório público)")
    return errors


def is_final(case: Mapping[str, Any]) -> bool:
    """True when the label has passed the second-labeler check (agreed) or was settled by a human."""
    return case.get("review_status") in FINAL_STATUSES


def _reject_duplicate_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    keys = [key for key, _ in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise ValueError(f"chave duplicada no JSON: {repeated}")
    return dict(pairs)


def _read_document(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, ValueError) as exc:  # ValueError covers JSONDecodeError and UnicodeDecodeError
        raise CalibrationDataError(f"{path}: não foi possível ler: {exc}") from None
    if not isinstance(doc, dict):
        raise CalibrationDataError(f"{path}: o documento deve ser um objeto")
    version = doc.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise CalibrationDataError(f"{path}: schema_version deve ser {SCHEMA_VERSION}")
    if doc.get("surface") not in SURFACES:
        raise CalibrationDataError(f"{path}: surface deve ser um de {list(SURFACES)}")
    if not isinstance(doc.get("cases"), list):
        raise CalibrationDataError(f"{path}: 'cases' deve ser uma lista")
    problems = _freeze_problems(doc)
    if problems:
        raise CalibrationDataError(f"{path}: " + "; ".join(problems))
    return doc


def _freeze_problems(doc: Mapping[str, Any]) -> List[str]:
    split = doc.get("split")
    freeze = doc.get("freeze")
    if split not in (None, SPLIT_HELDOUT):
        return [f"split deve ser ausente ou '{SPLIT_HELDOUT}'"]
    if split is None:
        return ["freeze só existe em um conjunto 'heldout'"] if freeze is not None else []
    if doc.get("surface") != SURFACE_GUARD:
        return ["um conjunto 'heldout' só existe na superfície guard"]
    if not isinstance(freeze, dict) or set(freeze) != set(FREEZE_KEYS):
        return [f"um conjunto 'heldout' exige freeze com {list(FREEZE_KEYS)}"]
    problems: List[str] = []
    if not (isinstance(freeze["rules_sha256_16"], str) and _FREEZE_HASH_RE.fullmatch(freeze["rules_sha256_16"])):
        problems.append("freeze.rules_sha256_16 deve ter 16 hex minúsculos")
    if not (isinstance(freeze["frozen_on"], str) and _FREEZE_DATE_RE.fullmatch(freeze["frozen_on"])):
        problems.append("freeze.frozen_on deve ser AAAA-MM-DD")
    return problems


def rules_changed_since_freeze(doc: Mapping[str, Any]) -> bool:
    """True when ``doc`` is a frozen held-out set and the rules code differs from the one it was frozen with."""
    freeze = doc.get("freeze")
    return isinstance(freeze, dict) and freeze.get("rules_sha256_16") != rules_fingerprint()


def _normalize_state(text: str) -> str:
    return " ".join(text.split())


def _duplicate_errors(cases: Sequence[Mapping[str, Any]]) -> List[str]:
    """Flags repeated ids and repeated states (ignoring whitespace differences). Only str values count."""
    errors: List[str] = []
    for key, normalize in (("id", str), ("state", _normalize_state)):
        seen: Dict[str, Any] = {}
        for case in cases:
            value = case.get(key) if isinstance(case, dict) else None
            if not isinstance(value, str):
                continue
            norm = normalize(value)
            if norm in seen:
                errors.append(f"{case.get('id')}: {key} duplicado de {seen[norm]}")
            else:
                seen[norm] = case.get("id")
    return errors


def load_cases(path: str, require_final: bool = False) -> List[Dict[str, Any]]:
    """Loads one dataset file. Raises CalibrationDataError listing every problem found."""
    doc = _read_document(path)
    surface = doc["surface"]
    cases = doc["cases"]
    heldout = doc.get("split") == SPLIT_HELDOUT
    unchanged = not rules_changed_since_freeze(doc)
    problems: List[str] = []
    for index, case in enumerate(cases):
        label = case.get("id", f"#{index}") if isinstance(case, dict) else f"#{index}"
        errors = validate_case(case, surface, heldout=heldout, rules_unchanged=unchanged)
        problems.extend(f"{label}: {err}" for err in errors)
        if require_final and isinstance(case, dict) and not is_final(case):
            problems.append(f"{label}: rótulo ainda não revisado (review_status={case.get('review_status')!r})")
    problems.extend(_duplicate_errors(cases))
    if problems:
        raise CalibrationDataError(f"{path}:\n  " + "\n  ".join(problems))
    return json.loads(json.dumps(cases))


def load_dir(directory: str = DEFAULT_DATA_DIR, require_final: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    """Loads every dataset file in ``directory``, grouped by surface.

    Fails on unexpected files (anything but ``*.json`` and ``README.md``), on an empty directory,
    and on duplicate ids or states across files, so nothing is skipped silently.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {surface: [] for surface in SURFACES}
    problems: List[str] = []
    loaded_files = 0
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if name == "README.md" and os.path.isfile(path):
            continue
        if not (name.endswith(".json") and os.path.isfile(path)):
            problems.append(f"{name}: arquivo inesperado (esperado *.json em minúsculas ou README.md)")
            continue
        grouped[_read_document(path)["surface"]].extend(load_cases(path, require_final=require_final))
        loaded_files += 1
    if not loaded_files:
        problems.append("nenhum arquivo de dataset *.json encontrado")
    problems.extend(_duplicate_errors([c for cases in grouped.values() for c in cases]))
    if problems:
        raise CalibrationDataError(f"{directory}:\n  " + "\n  ".join(problems))
    return grouped


def read_freeze(directory: str = DEFAULT_DATA_DIR) -> Optional[Dict[str, Any]]:
    """Freeze info of the held-out file in ``directory``: ``{file, rules_sha256_16, frozen_on, rules_changed}``,
    or None when the directory has no held-out set."""
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not (name.endswith(".json") and os.path.isfile(path)):
            continue
        doc = _read_document(path)
        if doc.get("split") == SPLIT_HELDOUT:
            return {**doc["freeze"], "file": name, "rules_changed": rules_changed_since_freeze(doc)}
    return None


def _bool_counts(values: Sequence[bool]) -> Dict[str, int]:
    return {"true": sum(1 for v in values if v), "false": sum(1 for v in values if not v)}


def summarize(cases: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Counts used to judge whether a dataset is balanced and how much of it is reviewed."""
    summary: Dict[str, Any] = {
        "total": len(cases),
        "should_block": _bool_counts([c["should_block"] for c in cases]),
        "label_source": dict(Counter(c["label_source"] for c in cases)),
        "review_status": dict(Counter(c["review_status"] for c in cases)),
    }
    guard = [c for c in cases if isinstance(c.get("rules_catch"), bool)]
    if guard:
        summary["rules_catch"] = _bool_counts([c["rules_catch"] for c in guard])
        # The only cases where the model can still change the outcome.
        summary["blockable_missed_by_rules"] = sum(1 for c in guard if c["should_block"] and not c["rules_catch"])
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    require_final = "--require-final" in args
    positional = [arg for arg in args if arg != "--require-final"]
    directory = positional[0] if positional else DEFAULT_DATA_DIR
    try:
        grouped = load_dir(directory, require_final=require_final)
    except (CalibrationDataError, OSError) as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        return 1
    for surface, cases in grouped.items():
        if cases:
            print(f"{surface}: {json.dumps(summarize(cases), ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
