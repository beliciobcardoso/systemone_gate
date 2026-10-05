"""
Command Line Interface (CLI) for SystemOne Gate.
Provides dev tools and can start the MCP server directly.
"""

import sys
import subprocess
import json
import argparse
from contextlib import nullcontext
from typing import List, Optional

from .claude_hook import run_pretooluse
from .client import SystemOneClient
from .diff_review import DEFAULT_MAX_LINES_PER_FILE, format_coverage, review_staged
from .hooks import install_git_hook, uninstall_git_hook
from .mcp_server import run_mcp_server
from .output import plain_output
from .policy import (
    ACTION_BLOCK,
    DiffReview,
    InvalidResponse,
    PolicyConfig,
    evaluate_command,
    evaluate_diff,
    parse_command_check,
    parse_diff_review,
)

EXIT_CONFIG_ERROR = 2
GIT_DIFF_TIMEOUT_SECONDS = 30

def _try_parse(parser, res):
    """Parses a response for display only; the verdict comes from the policy layer."""
    try:
        return parser(res)
    except InvalidResponse:
        return None

def _print_diff_report(review: DiffReview, res: dict) -> None:
    risk_info = res["answers"]["risk_level"]
    print("\n------------------ Relatório de Impacto ------------------")
    print(f"📊 Nível de Risco Técnico: {review.risk_score:.2f} / 2.0")
    legend = risk_info.get("legend")
    level_probs = risk_info.get("probabilities")
    if isinstance(legend, dict) and isinstance(level_probs, dict):
        for idx, desc in legend.items():
            prob = level_probs.get(idx, 0.0)
            print(f"   • Nível {idx} ({prob*100:.1f}%): {desc}")

    print(f"\n⚠️  Breaking Change: {review.breaking_choice.upper()}")
    for opt, prob in review.breaking_probs.items():
        print(f"   • {opt}: {prob*100:.1f}%")
    print("----------------------------------------------------------\n")

def handle_diff(client: SystemOneClient, model: str, max_lines: int = DEFAULT_MAX_LINES_PER_FILE) -> int:
    try:
        diff_output = subprocess.check_output(["git", "diff", "--cached"], text=True,
                                              timeout=GIT_DIFF_TIMEOUT_SECONDS)
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

    try:
        cfg = PolicyConfig.from_env()
    except ValueError as e:
        print(f"❌ Configuração de política inválida: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    decision = evaluate_diff(res, cfg)
    if decision.warning:
        print(f"⚠️  [SystemOne Gate] Aviso: {decision.warning}", file=sys.stderr)

    review = _try_parse(parse_diff_review, res)
    if review is not None:
        _print_diff_report(review, res)

    if decision.action == ACTION_BLOCK:
        if review is not None:
            print("❌ [BLOQUEIO ATIVADO] Risco crítico e quebra de contrato identificados!", file=sys.stderr)
        else:
            print(f"❌ [BLOQUEIO ATIVADO] {'; '.join(decision.reasons)}", file=sys.stderr)
        return 1

    if decision.warning:
        return 0  # Falha aberta (diff_on_error=allow): não trava trabalho offline sem ollama
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
    try:
        cfg = PolicyConfig.from_env()
    except ValueError as e:
        print(f"❌ Configuração de política inválida: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    decision = evaluate_command(res, cfg)
    if decision.warning:
        print(f"⚠️  [SystemOne Gate] Aviso: {decision.warning}", file=sys.stderr)

    check = _try_parse(parse_command_check, res)
    if check is not None:
        print(f"🛡️  Comando: {command_text}")
        print(f"• Destrutivo: {check.choice}")
        print(f"• Pontuação de perigo: {check.danger_score:.2f} / 2.0")

    if decision.action == ACTION_BLOCK:
        if check is not None:
            print("❌ [COMANDO BLOQUEADO] Risco destrutivo elevado!", file=sys.stderr)
        else:
            print(f"❌ [COMANDO BLOQUEADO] {'; '.join(decision.reasons)}", file=sys.stderr)
        return 1
    return 0

def main(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(
        prog="systemone-gate",
        description="Gatekeeper e motor de triagem local de baixa latência para agentes de IA e desenvolvedores."
    )
    parser.add_argument("--plain", action="store_true",
                        help="Saída apenas ASCII (sem emoji); também via SYSTEMONE_PLAIN=1")
    subparsers = parser.add_subparsers(dest="command", help="Comandos disponíveis")

    # diff
    p_diff = subparsers.add_parser("diff", help="Inspeciona alterações staged (git diff --cached)")
    p_diff.add_argument("--nimble", action="store_true", help="Usa Nimble (9B) em vez do Tev1 padrão")

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

    # uninstall-hook
    p_unhook = subparsers.add_parser("uninstall-hook", help="Remove o pre-commit hook do SystemOne Gate (restaura o backup, se houver)")
    p_unhook.add_argument("--repo", default=None, help="Caminho do repositório Git")

    # hook-guard
    subparsers.add_parser("hook-guard", help="Hook PreToolUse do Claude Code: bloqueia comandos catastróficos (offline, sem modelo)")

    # mcp
    subparsers.add_parser("mcp", help="Inicia o servidor MCP stdio (para Claude Desktop, Cursor, Antigravity)")

    args = parser.parse_args(argv)
    # The MCP server speaks JSON on stdout: it must never go through the translator.
    output_ctx = nullcontext() if args.command == "mcp" else plain_output(args.plain)
    with output_ctx:
        _dispatch(parser, args)

def _dispatch(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    try:
        client = SystemOneClient()
    except ValueError as e:
        print(f"❌ Configuração inválida: {e}", file=sys.stderr)
        sys.exit(2)

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

    elif args.command == "uninstall-hook":
        success = uninstall_git_hook(args.repo)
        sys.exit(0 if success else 1)

    elif args.command == "hook-guard":
        code, message = run_pretooluse(sys.stdin.read())
        if message:
            print(message, file=sys.stderr)
        sys.exit(code)

    elif args.command == "mcp":
        run_mcp_server()

    else:
        parser.print_help()
        sys.exit(0)

if __name__ == "__main__":
    main()
