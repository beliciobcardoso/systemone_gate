"""
Standard decision rubrics for SystemOne Gate.
Designed according to Ollama /v1/systemone schema:
- 'choice': criteria is a dict of category keys mapped to description or null.
- 'score': criteria is an ordered list/array of string descriptions.

The diff-risk and error-triage rubrics are language-sensitive and come in
profiles (see PROFILES, get_diff_rubric, get_triage_rubric). The module
constants RUBRIC_DIFF_RISK / RUBRIC_ERROR_TRIAGE are the "default" profile.

All rubric text sent to the model is in English: the project is public and
international, and the benchmark in docs/BENCHMARK_RUBRIC_LANGUAGE.md found no
detectable accuracy difference against Portuguese. Choice keys are the labels
the policy code reads and must not be translated or renamed.
tests/test_rubrics.py rejects Portuguese text in any rubric.
"""

import copy
from typing import Any, Callable, Dict

RUBRIC_DIFF_RISK = {
    "risk_level": {
        "type": "score",
        "instructions": "Rate the technical risk level of this code diff:",
        "criteria": [
            "Low: safe, documentation, comments or cosmetic refactoring",
            "Medium: new isolated function, simple bug fix with low coupling",
            "High: changes to concurrency, locks, memory allocation or socket structs"
        ]
    },
    "breaking_change": {
        "type": "choice",
        "instructions": "Does this change break public contracts, APIs or protocols?",
        "criteria": {
            "safe": None,
            "potential_break": None,
            "breaking_change": None
        }
    }
}

RUBRIC_ERROR_TRIAGE = {
    "root_cause": {
        "type": "choice",
        "instructions": "What is the main root cause of this failure or compilation/test error?",
        "criteria": {
            "compilation_syntax": None,
            "linker_undefined_reference": None,
            "memory_segfault_or_leak": None,
            "network_socket_timeout": None,
            "protocol_parsing_error": None,
            "test_assertion_failure": None,
            "environment_or_missing_dep": None
        }
    },
    "severity": {
        "type": "score",
        "instructions": "What is the severity level of this error?",
        "criteria": [
            "Non-blocking or cosmetic warning",
            "Partial failure or isolated test",
            "Critical blocking compilation or runtime error"
        ]
    }
}

DEFAULT_PROFILE = "default"
PROFILES = ("default", "generic", "web-backend")

_SEVERITY_QUESTION = {
    "type": "score",
    "instructions": "What is the severity level of this error?",
    "criteria": [
        "Non-blocking or cosmetic warning",
        "Partial failure or isolated test",
        "Critical blocking compilation or runtime error"
    ]
}


def _breaking_change_question(instructions: str) -> Dict[str, Any]:
    return {
        "type": "choice",
        "instructions": instructions,
        "criteria": {"safe": None, "potential_break": None, "breaking_change": None},
    }


def _generic_diff() -> Dict[str, Any]:
    return {
        "risk_level": {
            "type": "score",
            "instructions": "Rate the technical risk level of this code diff:",
            "criteria": [
                "Low: documentation, comments, tests or cosmetic refactoring with no behavior change",
                "Medium: isolated new feature or simple bug fix with low coupling",
                "High: changes to authentication, authorization, concurrency, handling of persisted "
                "data, sensitive configuration or the behavior of widely shared code"
            ]
        },
        "breaking_change": _breaking_change_question(
            "Does this change break public contracts, interfaces or data formats used by other components?"),
    }


def _web_backend_diff() -> Dict[str, Any]:
    return {
        "risk_level": {
            "type": "score",
            "instructions": (
                "Rate the technical risk level of this diff of a web backend service (API, database):"
            ),
            "criteria": [
                "Low: documentation, comments, tests or cosmetic refactoring with no behavior change",
                "Medium: new isolated endpoint or use case, simple bug fix with low coupling",
                "High: destructive or irreversible migration (dropping a column or table, narrowing a type, "
                "NOT NULL without a default); change to authentication or authorization, or removal of a "
                "permission check; query without a tenant or owner filter (cross-tenant leak); change to a "
                "public REST or GraphQL contract (removed or renamed field, changed status code); change to "
                "concurrency, transactions or locking; handling of secrets or sensitive configuration"
            ]
        },
        "breaking_change": _breaking_change_question(
            "Does this change break public contracts: the REST/GraphQL API (fields, routes, status codes), "
            "the database schema, or the schema of events and messages consumed by other services?"),
    }


def _generic_triage() -> Dict[str, Any]:
    return {
        "root_cause": {
            "type": "choice",
            "instructions": "What is the main root cause of this failure or compilation/test error?",
            "criteria": {
                "compilation_or_syntax_error": None,
                "dependency_or_environment": None,
                "runtime_exception": None,
                "resource_or_timeout": None,
                "invalid_input_or_data": None,
                "test_assertion_failure": None
            }
        },
        "severity": copy.deepcopy(_SEVERITY_QUESTION),
    }


def _web_backend_triage() -> Dict[str, Any]:
    return {
        "root_cause": {
            "type": "choice",
            "instructions": (
                "What is the main root cause of this failure or build/test/runtime error of the backend "
                "service?"
            ),
            "criteria": {
                "compilation_or_type_error": None,
                "dependency_or_environment": None,
                "database_or_migration_error": None,
                "authentication_or_permission_error": None,
                "validation_or_contract_error": None,
                "network_or_timeout": None,
                "test_assertion_failure": None,
                "unhandled_runtime_exception": None
            }
        },
        "severity": copy.deepcopy(_SEVERITY_QUESTION),
    }


_DIFF_PROFILES: Dict[str, Callable[[], Dict[str, Any]]] = {
    "default": lambda: copy.deepcopy(RUBRIC_DIFF_RISK),
    "generic": _generic_diff,
    "web-backend": _web_backend_diff,
}
_TRIAGE_PROFILES: Dict[str, Callable[[], Dict[str, Any]]] = {
    "default": lambda: copy.deepcopy(RUBRIC_ERROR_TRIAGE),
    "generic": _generic_triage,
    "web-backend": _web_backend_triage,
}


def _resolve(registry: Dict[str, Callable[[], Dict[str, Any]]], profile: str) -> Dict[str, Any]:
    factory = registry.get(profile) if isinstance(profile, str) else None
    if factory is None:
        raise ValueError(f"Perfil de rubrica inválido: {profile!r} (válidos: {', '.join(PROFILES)})")
    return factory()


def get_diff_rubric(profile: str = DEFAULT_PROFILE) -> Dict[str, Any]:
    """Fresh copy of the diff-risk rubric for the given profile."""
    return _resolve(_DIFF_PROFILES, profile)


def get_triage_rubric(profile: str = DEFAULT_PROFILE) -> Dict[str, Any]:
    """Fresh copy of the error-triage rubric for the given profile."""
    return _resolve(_TRIAGE_PROFILES, profile)


RUBRIC_COMMAND_SAFETY = {
    "is_destructive": {
        "type": "choice",
        "instructions": "Can this command delete data, overwrite files in bulk or misconfigure the environment?",
        "criteria": {
            "safe": None,
            "destructive_or_risky": None
        }
    },
    "danger_score": {
        "type": "score",
        "instructions": "Danger level of the command:",
        "criteria": [
            "Safe read or build command (e.g. ls, git status, cargo check, make)",
            "Command that modifies working files or the local environment in a controlled way",
            "Dangerous command: mass deletion, elevated privileges or irreversible impact"
        ]
    }
}

RUBRIC_AGENT_ROUTING = {
    "assigned_specialist": {
        "type": "choice",
        "instructions": "Which type of AI specialist should handle this developer request?",
        "criteria": {
            "researcher": None,
            "code_architect": None,
            "test_engineer": None,
            "security_auditor": None,
            "devops_deploy": None
        }
    },
    "task_complexity": {
        "type": "score",
        "instructions": "Estimated task complexity:",
        "criteria": [
            "Simple: quick lookup, one-line tweak or one-off explanation",
            "Medium: implementing a function or fixing a specific bug",
            "High: broad refactoring, system design or deep investigation"
        ]
    }
}
