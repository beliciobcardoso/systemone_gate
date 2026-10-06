# Rules for Cursor IDE (.cursorrules)

🌐 **English** · [Português (Brasil)](cursor_rules.pt-BR.md)

Copy the content below into a file named `.cursorrules` at the root of your project.

```markdown
# Cursor Agent Guidelines: SystemOne Gatekeeper

1. **Error triage**:
   - Whenever a terminal command (`make`, `cargo`, `npm test`, etc.) fails, call the MCP tool `systemone_triage_error` with the error log.
   - Use the root cause identified by the local model to guide the fix before rewriting files.

2. **Terminal execution safety**:
   - Before proposing destructive commands (deleting files, database resets, container cleanup), ask the user for explicit confirmation.
   - `systemone_command_guard` is only a heuristic warning: the model's "safe" verdict does NOT authorize execution and does not replace external enforcement (see "Real enforcement in Claude Code" in the manual).

3. **Diff review**:
   - After finishing a large change in C/C++ code, run the diff risk check through the MCP tool `systemone_review_diff`.
```
