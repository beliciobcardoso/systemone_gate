import json

import pytest

from systemone_gate import rubrics

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
