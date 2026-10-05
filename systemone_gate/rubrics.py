"""
Standard decision rubrics for SystemOne Gate.
Designed according to Ollama /v1/systemone schema:
- 'choice': criteria is a dict of category keys mapped to description or null.
- 'score': criteria is an ordered list/array of string descriptions.

The diff-risk and error-triage rubrics are language-sensitive and come in
profiles (see PROFILES, get_diff_rubric, get_triage_rubric). The module
constants RUBRIC_DIFF_RISK / RUBRIC_ERROR_TRIAGE are the "default" profile.
"""

import copy
from typing import Any, Callable, Dict

RUBRIC_DIFF_RISK = {
    "risk_level": {
        "type": "score",
        "instructions": "Avalie o nível de risco técnico deste diff de código:",
        "criteria": [
            "Baixo: seguro, documentação, comentários ou refatoração cosmética",
            "Médio: nova função isolada, correção simples de bug com baixo acoplamento",
            "Alto: modificação em concorrência, locks, alocação de memória ou structs de socket"
        ]
    },
    "breaking_change": {
        "type": "choice",
        "instructions": "Esta alteração quebra contratos públicos, APIs ou protocolos?",
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
        "instructions": "Qual é a causa-raiz principal desta falha ou erro de compilação/teste?",
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
        "instructions": "Qual o nível de gravidade deste erro?",
        "criteria": [
            "Aviso não bloqueante ou estético",
            "Falha parcial ou teste isolado",
            "Erro bloqueante crítico de compilação ou execução"
        ]
    }
}

DEFAULT_PROFILE = "default"
PROFILES = ("default", "generic", "web-backend")

_SEVERITY_QUESTION = {
    "type": "score",
    "instructions": "Qual o nível de gravidade deste erro?",
    "criteria": [
        "Aviso não bloqueante ou estético",
        "Falha parcial ou teste isolado",
        "Erro bloqueante crítico de compilação ou execução"
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
            "instructions": "Avalie o nível de risco técnico deste diff de código:",
            "criteria": [
                "Baixo: documentação, comentários, testes ou refatoração cosmética sem mudança de comportamento",
                "Médio: nova funcionalidade isolada ou correção simples de bug com baixo acoplamento",
                "Alto: mudança em autenticação, autorização, concorrência, tratamento de dados "
                "persistidos, configuração sensível ou comportamento de código amplamente compartilhado"
            ]
        },
        "breaking_change": _breaking_change_question(
            "Esta alteração quebra contratos públicos, interfaces ou formatos de dados usados por outros componentes?"),
    }


def _web_backend_diff() -> Dict[str, Any]:
    return {
        "risk_level": {
            "type": "score",
            "instructions": (
                "Avalie o nível de risco técnico deste diff de um serviço backend web (API, banco de "
                "dados):"
            ),
            "criteria": [
                "Baixo: documentação, comentários, testes ou refatoração cosmética sem mudança de comportamento",
                "Médio: novo endpoint ou caso de uso isolado, correção simples de bug com baixo acoplamento",
                "Alto: migration destrutiva ou irreversível (drop de coluna ou tabela, estreitamento de tipo, "
                "NOT NULL sem default); alteração de autenticação ou autorização, ou remoção de verificação "
                "de permissão; query sem filtro de tenant ou dono (vazamento entre tenants); mudança de "
                "contrato público REST ou GraphQL (campo removido ou renomeado, status code alterado); "
                "mudança em concorrência, transações ou locking; manipulação de segredos ou configuração sensível"
            ]
        },
        "breaking_change": _breaking_change_question(
            "Esta alteração quebra contratos públicos: API REST/GraphQL (campos, rotas, status codes), "
            "schema do banco de dados ou schema de eventos e mensagens consumidos por outros serviços?"),
    }


def _generic_triage() -> Dict[str, Any]:
    return {
        "root_cause": {
            "type": "choice",
            "instructions": "Qual é a causa-raiz principal desta falha ou erro de compilação/teste?",
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
                "Qual é a causa-raiz principal desta falha ou erro de build/teste/execução do serviço "
                "backend?"
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
        "instructions": "Este comando pode apagar dados, sobrescrever arquivos em lote ou desconfigurar o ambiente?",
        "criteria": {
            "safe": None,
            "destructive_or_risky": None
        }
    },
    "danger_score": {
        "type": "score",
        "instructions": "Nível de periculosidade do comando:",
        "criteria": [
            "Comando de leitura ou build seguro (ex: ls, git status, cargo check, make)",
            "Comando que modifica arquivos de trabalho ou ambiente local de forma controlada",
            "Comando perigoso de exclusão em massa, privilégio elevado ou impacto irreversível"
        ]
    }
}

RUBRIC_AGENT_ROUTING = {
    "assigned_specialist": {
        "type": "choice",
        "instructions": "Qual tipo de especialista de IA deve resolver esta solicitação do desenvolvedor?",
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
        "instructions": "Complexidade estimada da tarefa:",
        "criteria": [
            "Simples: consulta rápida, ajuste de 1 linha ou explicação pontual",
            "Média: implementação de função ou correção de bug pontual",
            "Alta: refatoração ampla, design de sistema ou investigação profunda"
        ]
    }
}
