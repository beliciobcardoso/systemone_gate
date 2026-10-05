#!/usr/bin/env python3
"""
Exemplo de integração do SystemOne Gate em um loop de agente autônomo.
Demonstra:
1. Roteamento de intenção com Nimble 9B
2. Guardrail de comando antes de executar bash
3. Triagem de exceções de compilação
"""

from systemone_gate import SystemOneClient

def main():
    client = SystemOneClient()

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
        is_dest = res.get("answers", {}).get("is_destructive", {}).get("choice", "safe")
        danger = res.get("answers", {}).get("danger_score", {}).get("score", 0.0)
        status = "❌ BLOQUEADO" if (is_dest != "safe" and danger > 1.4) else "✅ PERMITIDO"
        print(f"[{status}] Comando: '{cmd}' | Destrutivo: {is_dest} | Perigo: {danger:.2f}")

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
