import json

import pytest

from systemone_gate import doctor, rubrics

ALL = {
    "RUBRIC_DIFF_RISK": rubrics.RUBRIC_DIFF_RISK,
    "RUBRIC_ERROR_TRIAGE": rubrics.RUBRIC_ERROR_TRIAGE,
    "RUBRIC_COMMAND_SAFETY": rubrics.RUBRIC_COMMAND_SAFETY,
    "RUBRIC_AGENT_ROUTING": rubrics.RUBRIC_AGENT_ROUTING,
}
QUESTIONS = [(r, k, q) for r, d in ALL.items() for k, q in d.items()]
IDS = ["%s.%s" % (r, k) for r, k, _ in QUESTIONS]


@pytest.mark.parametrize("rubric,key,q", QUESTIONS, ids=IDS)
def test_question_structure(rubric, key, q):
    assert q["type"] in {"choice", "score"}
    assert isinstance(q["instructions"], str) and q["instructions"].strip()
    if q["type"] == "choice":
        crit = q["criteria"]
        assert isinstance(crit, dict) and len(crit) >= 2
        assert all(v is None or isinstance(v, str) for v in crit.values())
    else:
        crit = q["criteria"]
        assert isinstance(crit, list) and len(crit) >= 2
        assert all(isinstance(c, str) and c.strip() for c in crit)


@pytest.mark.parametrize("name,expected", [
    ("RUBRIC_DIFF_RISK", {"risk_level", "breaking_change"}),
    ("RUBRIC_ERROR_TRIAGE", {"root_cause", "severity"}),
    ("RUBRIC_COMMAND_SAFETY", {"is_destructive", "danger_score"}),
    ("RUBRIC_AGENT_ROUTING", {"assigned_specialist", "task_complexity"}),
])
def test_keys_referenced_by_code_exist(name, expected):
    assert expected <= set(ALL[name])


def test_referenced_choice_options_used_by_cli():
    assert {"safe", "destructive_or_risky"} <= set(rubrics.RUBRIC_COMMAND_SAFETY["is_destructive"]["criteria"])
    assert "breaking_change" in rubrics.RUBRIC_DIFF_RISK["breaking_change"]["criteria"]


@pytest.mark.parametrize("name", sorted(ALL))
def test_json_serializable(name):
    assert json.loads(json.dumps(ALL[name])) == ALL[name]


# ---------------------------------------------------------------- language policy: rubrics are English

# Portuguese function words and very common vocabulary that are not English words (so "do" and "no", which
# are English too, are left out). Accented letters are caught separately; this list catches unaccented
# Portuguese ("Seguro para uso com dados").
PORTUGUESE_WORDS = frozenset(
    """
    de da dos das na nos nas ao aos pelo pela pelos pelas por para com sem ou em uma um uns umas
    este esta estes estas esse essa esses essas deste desta destes destas neste nesta nesse nessa
    que qual quais quando onde como mais menos muito pouco ser sao nao ha foi mas se seu sua seus suas
    dados arquivo arquivos comando erro causa risco nivel gravidade baixo medio alto aviso falha teste
    seguro perigoso mudanca alteracao quebra contrato contratos exclusao
    """.split()
)


def _every_rubric():
    rubric_sets = dict(ALL)
    for profile in rubrics.PROFILES:
        rubric_sets[f"diff:{profile}"] = rubrics.get_diff_rubric(profile)
        rubric_sets[f"triage:{profile}"] = rubrics.get_triage_rubric(profile)
    # Rubrics defined outside rubrics.py are also sent to the model.
    rubric_sets["doctor:smoke"] = doctor.SMOKE_RUBRIC
    return rubric_sets


def _texts(rubric):
    """Every string the model sees: instructions, score criteria, choice keys and descriptions."""
    for key, question in rubric.items():
        yield key
        yield question["instructions"]
        criteria = question["criteria"]
        if isinstance(criteria, dict):
            for option, description in criteria.items():
                yield option
                if description is not None:
                    yield description
        else:
            yield from criteria


def _non_english_letters(text):
    """Accented or non-Latin letters. Plain punctuation (dashes, quotes) is not a language signal."""
    return sorted({ch for ch in text if ord(ch) > 127 and ch.isalpha()})


def _portuguese_words(text):
    words = {w.strip(".,:;()/'\"?!").lower() for w in text.replace("_", " ").split()}
    return sorted(words & PORTUGUESE_WORDS)


def _language_problems(texts):
    """Human-readable problems for every text that does not look English."""
    problems = []
    for text in texts:
        letters, words = _non_english_letters(text), _portuguese_words(text)
        if letters:
            problems.append(f"letters {letters} in {text!r}")
        if words:
            problems.append(f"Portuguese words {words} in {text!r}")
    return problems


@pytest.mark.parametrize("name", sorted(_every_rubric()))
def test_rubric_text_is_english(name):
    problems = _language_problems(_texts(_every_rubric()[name]))
    assert not problems, f"{name}: rubrics must be English: {problems}"


@pytest.mark.parametrize(
    "text",
    [
        "Qual o risco deste diff",  # unaccented Portuguese
        "Seguro e rapido",
        "Comando que modifica arquivos de trabalho",
        "Avalie o nível de risco",  # accented
        "Mudança em autenticação",
    ],
)
def test_the_language_guard_rejects_portuguese(text):
    assert _language_problems([text]), text


@pytest.mark.parametrize(
    "text",
    [
        "Safe read or build command (e.g. ls, git status, cargo check, make)",
        "Low: safe \u2014 documentation, comments or cosmetic refactoring",  # an em dash is not a language signal
        "High: changes to concurrency, locks, memory allocation or socket structs",
        "Is the statement true? Statement: 1 + 1 = 2",
        "database_or_migration_error",
    ],
)
def test_the_language_guard_accepts_english(text):
    assert not _language_problems([text]), text
