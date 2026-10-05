"""English translations of the default-profile rubrics (experiment RSK-02 / S3).

Faithful translation only: same question keys, same choice keys (they are the
labels), same number and order of score criteria. Only `instructions` and the
score `criteria` descriptions are translated.
"""

RUBRIC_DIFF_RISK_EN = {
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

RUBRIC_ERROR_TRIAGE_EN = {
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
