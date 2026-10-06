#!/usr/bin/env python3
"""
Example of integrating SystemOne Gate into an autonomous agent loop.
Demonstrates:
1. Intent routing with Nimble 9B
2. Command guardrail before running bash
3. Triage of build errors
"""

from systemone_gate import PolicyConfig, SystemOneClient, evaluate_command


def main():
    client = SystemOneClient()
    policy = PolicyConfig.from_env()  # thresholds/failure handling configurable via SYSTEMONE_* (not calibrated)

    print("=== 1. Task Routing ===")
    prompt = "Refactor the socket layer to use asynchronous epoll"
    route = client.route_task(prompt)
    answers = route.get("answers", {})
    specialist = answers.get("assigned_specialist", {}).get("choice", "unknown")
    complexity = answers.get("task_complexity", {}).get("score", 0.0)
    print(f"Prompt: {prompt}")
    print(f"-> Suggested specialist: {specialist}")
    print(f"-> Estimated complexity (0 to 2): {complexity:.2f}\n")

    print("=== 2. Command Safety Guardrail ===")
    commands = [
        "git status",
        "make -j4",
        "rm -rf /var/lib/mosquitto/*"
    ]
    for cmd in commands:
        res = client.guard_command(cmd)
        decision = evaluate_command(res, policy)
        status = "❌ BLOCKED" if decision.action == "block" else "✅ ALLOWED"
        detail = "; ".join(decision.reasons) or decision.warning or "no caveats"
        print(f"[{status}] Command: '{cmd}' | {detail}")

    print("\n=== 3. Build Error Triage ===")
    error_trace = "undefined reference to `mqtt3_db_open` in mosquitto.c:45"
    triage = client.triage_error(error_trace)
    triage_answers = triage.get("answers", {})
    root_cause = triage_answers.get("root_cause", {}).get("choice", "unknown")
    severity = triage_answers.get("severity", {}).get("score", 0.0)
    print(f"Error: {error_trace}")
    print(f"-> Root cause: {root_cause}")
    print(f"-> Severity (0 to 2): {severity:.2f}")

if __name__ == "__main__":
    main()
