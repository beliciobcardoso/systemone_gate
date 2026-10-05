"""
Standard decision rubrics for SystemOne Gate.
Designed according to Ollama /v1/systemone schema:
- 'choice': criteria is a dict of category keys mapped to description or null.
- 'score': criteria is an ordered list/array of string descriptions.
"""

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
