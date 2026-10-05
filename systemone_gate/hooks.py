"""
Git Hooks management for SystemOne Gate.
Allows automatic installation of pre-commit diff checks in any git repository.
"""

import os
import shlex
import sys
import subprocess
from typing import Optional

HOOK_MARKER = "SystemOne Gate"
PYTHON_PLACEHOLDER = "@@PYTHON@@"
GIT_TIMEOUT_SECONDS = 10
GIT_NOT_FOUND_MESSAGE = (
    "❌ Erro: Diretório .git não encontrado. Certifique-se de estar dentro de um repositório Git."
)

PRE_COMMIT_TEMPLATE = """#!/bin/sh
# SystemOne Gate Git Pre-Commit Hook
# Automatically inspects staged changes with local Ollama System One

# Hook original preservado na instalação: roda primeiro e, se falhar, bloqueia.
BACKUP="$(dirname "$0")/$(basename "$0").backup"
if [ -x "$BACKUP" ]; then
    "$BACKUP" "$@"
    BACKUP_STATUS=$?
    if [ $BACKUP_STATUS -ne 0 ]; then
        exit $BACKUP_STATUS
    fi
fi

PY=@@PYTHON@@

# Nunca bloquear o usuário se o pacote não estiver disponível neste interpretador.
if ! "$PY" -c "import systemone_gate" >/dev/null 2>&1; then
    echo "[SystemOne Gate] pacote indisponível, pulando verificação." >&2
    exit 0
fi

"$PY" -m systemone_gate.cli diff "$@"
STATUS=$?

if [ $STATUS -ne 0 ]; then
    echo ""
    echo "❌ [SystemOne Gate] Commit abortado por risco detectado."
    echo "💡 Para forçar o commit ignorando a verificação, use: git commit --no-verify"
    exit $STATUS
fi

exit 0
"""

def render_hook_script(python_executable: str) -> str:
    return PRE_COMMIT_TEMPLATE.replace(PYTHON_PLACEHOLDER, shlex.quote(python_executable))

def _is_ours(hook_path: str) -> bool:
    with open(hook_path, "r", encoding="utf-8", errors="ignore") as f:
        return HOOK_MARKER in f.read()

def find_git_root(start_path: Optional[str] = None) -> Optional[str]:
    """Walk up from start_path looking for a ``.git`` entry.

    ``.git`` is a directory in regular clones but a *file* in linked worktrees
    and submodules, so existence (not isdir) is what matters. Hook installation
    does not rely on this: it asks git itself (see _resolve_hooks_dir).
    """
    curr = os.path.abspath(start_path or os.getcwd())
    while True:
        if os.path.exists(os.path.join(curr, ".git")):
            return curr
        parent = os.path.dirname(curr)
        if parent == curr:
            return None
        curr = parent

def _resolve_hooks_dir(repo_path: Optional[str] = None) -> Optional[str]:
    """Ask git where hooks live (honors worktrees, submodules, core.hooksPath).

    `git rev-parse --git-path hooks` prints a path relative to the -C directory
    unless it is already absolute, so it is joined against that directory
    manually (works on old gits without --path-format=absolute).
    """
    base = os.path.abspath(repo_path or os.getcwd())
    try:
        result = subprocess.run(
            ["git", "-C", base, "rev-parse", "--git-path", "hooks"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    output = result.stdout.strip() if result is not None and result.returncode == 0 else ""
    if not output:
        print(GIT_NOT_FOUND_MESSAGE, file=sys.stderr)
        return None
    return os.path.normpath(os.path.join(base, output))

def install_git_hook(repo_path: Optional[str] = None, hook_name: str = "pre-commit") -> bool:
    hooks_dir = _resolve_hooks_dir(repo_path)
    if not hooks_dir:
        return False

    os.makedirs(hooks_dir, exist_ok=True)
    target_hook = os.path.join(hooks_dir, hook_name)
    backup_path = f"{target_hook}.backup"

    if os.path.exists(target_hook) and not _is_ours(target_hook):
        if os.path.exists(backup_path):
            print(
                f"❌ Erro: hook existente não é do SystemOne Gate e já há um backup em {backup_path}. "
                "Nada foi alterado; resolva manualmente para não perder dados.",
                file=sys.stderr,
            )
            return False
        print(f"⚠️  Hook existente encontrado. Criando backup em {backup_path}")
        os.rename(target_hook, backup_path)

    with open(target_hook, "w", encoding="utf-8") as f:
        f.write(render_hook_script(sys.executable))

    os.chmod(target_hook, 0o755)
    print(f"✅ Hook '{hook_name}' instalado com sucesso em: {target_hook}")
    return True

def uninstall_git_hook(repo_path: Optional[str] = None, hook_name: str = "pre-commit") -> bool:
    hooks_dir = _resolve_hooks_dir(repo_path)
    if not hooks_dir:
        return False
    target_hook = os.path.join(hooks_dir, hook_name)
    if not os.path.exists(target_hook):
        print(f"Aviso: Hook '{hook_name}' não encontrado em {target_hook}")
        return False
    if not _is_ours(target_hook):
        print(f"❌ Erro: o hook em {target_hook} não é do SystemOne Gate; nada foi removido.", file=sys.stderr)
        return False

    backup_path = f"{target_hook}.backup"
    if os.path.exists(backup_path):
        os.replace(backup_path, target_hook)
        print(f"✅ Hook '{hook_name}' original restaurado em {target_hook}")
    else:
        os.remove(target_hook)
        print(f"✅ Hook '{hook_name}' removido de {target_hook}")
    return True
