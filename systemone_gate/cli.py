"""
Command Line Interface (CLI) for SystemOne Gate.
Provides dev tools and can start the MCP server directly.
"""

import sys
import subprocess
import json
import argparse
from typing import List

from .client import SystemOneClient
from .diff_review import format_coverage, review_staged
from .hooks import install_git_hook, uninstall_git_hook
from .mcp_server import run_mcp_server

def handle_diff(client: SystemOneClient, model: str, max_lines: int = 250) -> int:
    try:
        diff_output = subprocess.check_output(["git", "diff", "--cached"], text=True)
    except Exception as e:
        print(f"❌ Erro ao executar 'git diff --cached': {e}", file=sys.stderr)
        return 1

    if not diff_output.strip():
        print("ℹ️  Nenhuma alteração staged encontrada (git diff --cached vazio). Commit liberado.")
        return 0

    line_count = len(diff_output.splitlines())
    print(f"🔍 [SystemOne Gate] Inspecionando diff ({line_count} linhas) com modelo '{model}'...")
    res = review_staged(client, diff_output, model, max_lines_per_file=max_lines)
    for coverage_line in format_coverage(res.get("coverage", {})):
        print(coverage_line)

    if "error" in res:
        print(f"⚠️  [SystemOne Gate] Aviso: {res['error']}", file=sys.stderr)
        return 0  # Falha silenciosa para não travar trabalho offline sem ollama

    answers = res.get("answers", {})
    risk_info = answers.get("risk_level", {})
    breaking_info = answers.get("breaking_change", {})

    risk_score = risk_info.get("score", 0.0)
    breaking_choice = breaking_info.get("choice", "safe")
    breaking_probs = breaking_info.get("probabilities", {})

    print("\n------------------ Relatório de Impacto ------------------")
    print(f"📊 Nível de Risco Técnico: {risk_score:.2f} / 2.0")
    if "legend" in risk_info:
        for idx, desc in risk_info["legend"].items():
            prob = risk_info.get("probabilities", {}).get(idx, 0.0)
            print(f"   • Nível {idx} ({prob*100:.1f}%): {desc}")

    print(f"\n⚠️  Breaking Change: {breaking_choice.upper()}")
    for opt, prob in breaking_probs.items():
        print(f"   • {opt}: {prob*100:.1f}%")
    print("----------------------------------------------------------\n")

    # Bloqueia se o risco for extremo e com quebra confirmada
    breaking_risk_prob = breaking_probs.get("breaking_change", 0.0)
    if risk_score > 1.85 and breaking_risk_prob > 0.65:
        print("❌ [BLOQUEIO ATIVADO] Risco crítico e quebra de contrato identificados!", file=sys.stderr)
        return 1

    print("✅ [APROVADO] Verificação concluída com sucesso.")
    return 0

def handle_triage(client: SystemOneClient, error_text: str, model: str) -> int:
    print(f"🩺 [SystemOne Gate] Triando erro com modelo '{model}'...\n")
    res = client.triage_error(error_text, model=model)

    if "error" in res:
        print(f"❌ Erro: {res['error']}", file=sys.stderr)
        return 1

    answers = res.get("answers", {})
    print(json.dumps(answers, indent=2, ensure_ascii=False))
    return 0

def handle_guard(client: SystemOneClient, command_text: str, model: str) -> int:
    res = client.guard_command(command_text, model=model)
    if "error" in res:
        print(f"❌ Erro: {res['error']}", file=sys.stderr)
        return 1

    answers = res.get("answers", {})
    is_dest = answers.get("is_destructive", {}).get("choice", "safe")
    danger = answers.get("danger_score", {}).get("score", 0.0)

    print(f"🛡️  Comando: {command_text}")
    print(f"• Destrutivo: {is_dest}")
    print(f"• Pontuação de perigo: {danger:.2f} / 2.0")

    if is_dest == "destructive_or_risky" and danger > 1.5:
        print("❌ [COMANDO BLOQUEADO] Risco destrutivo elevado!", file=sys.stderr)
        return 1
    return 0

def main(argv: List[str] = None):
    parser = argparse.ArgumentParser(
        prog="systemone-gate",
        description="Gatekeeper e motor de triagem local ultrarrápido para agentes de IA e desenvolvedores."
    )
    subparsers = parser.add_subparsers(dest="command", help="Comandos disponíveis")

    # diff
    p_diff = subparsers.add_parser("diff", help="Inspeciona alterações staged (git diff --cached)")
    p_diff.add_argument("--nimble", action="store_true", help="Usa Nimble (9B) em vez do Tev1 padrão")
    p_diff.add_argument("--tev", action="store_true", help="Força uso do Tev1 0.8B (mais rápido)")

    # triage
    p_triage = subparsers.add_parser("triage", help="Triagem de erros de compilação, testes ou logs")
    p_triage.add_argument("error_text", nargs="+", help="Texto do erro ou stacktrace")
    p_triage.add_argument("--model", default="nimble", help="Modelo Ollama a utilizar (padrão: nimble)")

    # guard
    p_guard = subparsers.add_parser("guard", help="Valida se um comando shell tem riscos de destruição de dados")
    p_guard.add_argument("cmd_text", nargs="+", help="Comando a ser inspecionado")

    # install-hook
    p_hook = subparsers.add_parser("install-hook", help="Instala o pre-commit hook no repositório Git atual")
    p_hook.add_argument("--repo", default=None, help="Caminho do repositório Git")

    # mcp
    subparsers.add_parser("mcp", help="Inicia o servidor MCP stdio (para Claude Desktop, Cursor, Antigravity)")

    args = parser.parse_args(argv)
    client = SystemOneClient()

    if args.command == "diff":
        model = "nimble" if args.nimble else "tev1:0.8b"
        sys.exit(handle_diff(client, model=model))

    elif args.command == "triage":
        err_msg = " ".join(args.error_text)
        sys.exit(handle_triage(client, err_msg, model=args.model))

    elif args.command == "guard":
        cmd_msg = " ".join(args.cmd_text)
        sys.exit(handle_guard(client, cmd_msg, model="tev1:0.8b"))

    elif args.command == "install-hook":
        success = install_git_hook(args.repo)
        sys.exit(0 if success else 1)

    elif args.command == "mcp":
        run_mcp_server()

    else:
        parser.print_help()
        sys.exit(0)

if __name__ == "__main__":
    main()
