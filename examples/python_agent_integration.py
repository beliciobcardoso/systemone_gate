#!/usr/bin/env python3
"""
Exemplo de integração do SystemOne Gate em um loop de agente autônomo.
Demonstra:
1. Roteamento de intenção com Nimble 9B
2. Guardrail de comando antes de executar bash
3. Triagem de exceções de compilação
"""

from systemone_gate import PolicyConfig, SystemOneClient, evaluate_command

def main():
    client = SystemOneClient()
    policy = PolicyConfig.from_env()  # limiares/falha configuráveis via SYSTEMONE_* (não calibrados)

    print("=== 1. Roteamento de Tarefa ===")
    prompt = "Refatore a estrutura de sockets para usar epoll assíncrono"
    route = client.route_task(prompt)
    answers = route.get("answers", {})
    specialist = answers.get("assigned_specialist", {}).get("choice", "unknown")
    complexity = answers.get("task_complexity", {}).get("score", 0.0)
    print(f"Prompt: {prompt}")
    print(f"-> Especialista indicado: {specialist}")
    print(f"-> Complexidade estimada (0 a 2): {complexity:.2f}\n")

    print("=== 2. Guardrail de Segurança de Comandos ===")
    commands = [
        "git status",
        "make -j4",
        "rm -rf /var/lib/mosquitto/*"
    ]
    for cmd in commands:
        res = client.guard_command(cmd)
        decision = evaluate_command(res, policy)
        status = "❌ BLOQUEADO" if decision.action == "block" else "✅ PERMITIDO"
        detail = "; ".join(decision.reasons) or decision.warning or "sem ressalvas"
        print(f"[{status}] Comando: '{cmd}' | {detail}")

    print("\n=== 3. Triagem de Erro de Build ===")
    error_trace = "undefined reference to `mqtt3_db_open` in mosquitto.c:45"
    triage = client.triage_error(error_trace)
    triage_answers = triage.get("answers", {})
    root_cause = triage_answers.get("root_cause", {}).get("choice", "desconhecido")
    severity = triage_answers.get("severity", {}).get("score", 0.0)
    print(f"Erro: {error_trace}")
    print(f"-> Causa-raiz: {root_cause}")
    print(f"-> Severidade (0 a 2): {severity:.2f}")

if __name__ == "__main__":
    main()
