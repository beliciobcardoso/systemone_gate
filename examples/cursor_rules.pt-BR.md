# Regras para Cursor IDE (.cursorrules)

🌐 [English](cursor_rules.md) · **Português (Brasil)**

Copie o conteúdo abaixo para um arquivo chamado `.cursorrules` na raiz do seu projeto.

```markdown
# Diretrizes do Agente Cursor: SystemOne Gatekeeper

1. **Triagem de Erros**:
   - Sempre que um comando de terminal (`make`, `cargo`, `npm test`, etc.) falhar, acione a ferramenta MCP `systemone_triage_error` passando o log de erro.
   - Use a causa raiz identificada pelo modelo local para orientar a correção antes de reescrever arquivos.

2. **Segurança de Execução no Terminal**:
   - Antes de propor comandos destrutivos (remoção de arquivos, resets de banco, limpeza de containers), peça confirmação explícita ao usuário.
   - `systemone_command_guard` é apenas um aviso heurístico: o veredito "safe" do modelo NÃO autoriza a execução e não substitui enforcement externo (veja "Enforcement real no Claude Code" no manual).

3. **Revisão de Diffs**:
   - Ao concluir uma alteração grande em código C/C++, execute a verificação de risco de diff via MCP `systemone_review_diff`.
```
