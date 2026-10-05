"""
Git Hooks management for SystemOne Gate.
Allows automatic installation of pre-commit diff checks in any git repository.
"""

import os
import sys
import subprocess
from typing import Optional

PRE_COMMIT_TEMPLATE = """#!/bin/sh
# SystemOne Gate Git Pre-Commit Hook
# Automatically inspects staged changes with local Ollama System One

python3 -m systemone_gate.cli diff "$@"
STATUS=$?

if [ $STATUS -ne 0 ]; then
    echo ""
    echo "❌ [SystemOne Gate] Commit abortado por risco detectado."
    echo "💡 Para forçar o commit ignorando a verificação, use: git commit --no-verify"
    exit $STATUS
fi

exit 0
"""

def find_git_root(start_path: Optional[str] = None) -> Optional[str]:
    curr = os.path.abspath(start_path or os.getcwd())
    while True:
        if os.path.isdir(os.path.join(curr, ".git")):
            return curr
        parent = os.path.dirname(curr)
        if parent == curr:
            return None
        curr = parent

def install_git_hook(repo_path: Optional[str] = None, hook_name: str = "pre-commit") -> bool:
    git_root = find_git_root(repo_path)
    if not git_root:
        print("❌ Erro: Diretório .git não encontrado. Certifique-se de estar dentro de um repositório Git.", file=sys.stderr)
        return False

    hooks_dir = os.path.join(git_root, ".git", "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    target_hook = os.path.join(hooks_dir, hook_name)

    # Backup se já existir e não for nosso
    if os.path.exists(target_hook):
        with open(target_hook, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        if "SystemOne Gate" not in content:
            backup_path = f"{target_hook}.backup"
            print(f"⚠️  Hook existente encontrado. Criando backup em {backup_path}")
            os.rename(target_hook, backup_path)

    with open(target_hook, "w", encoding="utf-8") as f:
        f.write(PRE_COMMIT_TEMPLATE)

    os.chmod(target_hook, 0o755)
    print(f"✅ Hook '{hook_name}' instalado com sucesso em: {target_hook}")
    return True

def uninstall_git_hook(repo_path: Optional[str] = None, hook_name: str = "pre-commit") -> bool:
    git_root = find_git_root(repo_path)
    if not git_root:
        return False
    target_hook = os.path.join(git_root, ".git", "hooks", hook_name)
    if os.path.exists(target_hook):
        os.remove(target_hook)
        print(f"✅ Hook '{hook_name}' removido de {target_hook}")
        return True
    print(f"Aviso: Hook '{hook_name}' não encontrado em {target_hook}")
    return False
