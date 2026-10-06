# 📘 SystemOne Gate Integration Manual for AI Agents

🌐 **English** · [Português (Brasil)](AGENT_MANUAL.pt-BR.md)

This guide explains how to connect **SystemOne Gate** to **any Artificial Intelligence agent** (Claude Code, Antigravity, Cursor, Windsurf, Cline, Roo Code, Aider, LangChain, CrewAI, AutoGen).

---

## 🧠 Why use SystemOne Gate with AI agents?

Generative AI agents (cloud models such as GPT, Claude and Gemini) are excellent at deep reasoning and code generation, but they are **slow and expensive** for repetitive background checks.

**SystemOne Gate** acts as the low-latency reflex system / motor cortex of the agents:

```mermaid
flowchart LR
    Agent[Generative AI Agent] --> |Check / Gating| Gate[SystemOne Gate]
    Gate --> |Ollama /v1/systemone| Engine[Nimble 9B / Tev1 0.8B]
    Engine --> |Model probabilities (low local latency; see README, Measured performance)| Gate
    Gate --> |Approval / Structured warning| Agent
```

* **No cloud token cost:** runs 100% on the local GPU/CPU (the cost becomes local hardware and memory).
* **Full privacy:** no code or diff leaves your machine.
* **Structured output:** no verbose text; only numeric model probabilities (not calibrated; see the `confidence` field) and typed labels (`choice` and `score`).

---

## ⚙️ Prerequisites and Environment Setup

Before configuring any agent, make sure the local Ollama backend is installed and the models are ready:

1. **Install or update Ollama to v0.35+**:
   ```bash
   # Linux
   curl -fsSL https://ollama.com/install.sh | sh

   # macOS / Windows
   # Download from https://ollama.com/download
   ```
2. **Check the version**:
   ```bash
   ollama -v  # Must be >= 0.35.0
   ```
3. **Pull the required models**:
   ```bash
   ollama pull tev1:0.8b  # Light, fast-reflex model (811MB; latency measured in the README)
   ollama pull nimble     # Accuracy model for code (9B, 9.5GB)
   ```
4. **Install the package locally**:
   ```bash
   git clone https://github.com/beliciobcardoso/systemone_gate.git
   cd systemone_gate
   pip install -e .
   ```

---

## 1. Antigravity (Google DeepMind)

Antigravity natively supports MCP servers and modular Skills.

Official docs: https://antigravity.google/docs/mcp and https://antigravity.google/docs/skills

### Step 1: Register the MCP server
Edit the global file `~/.gemini/config/mcp_config.json` (or `.agents/mcp_config.json` in the workspace):

```json
{
  "mcpServers": {
    "systemone": {
      "command": "/usr/bin/python3",
      "args": ["-m", "systemone_gate.mcp_server"],
      "env": {
        "PYTHONPATH": "/path/to/systemone_gate"
      }
    }
  }
}
```

### Step 2: Register the Skill
Create `~/.gemini/config/skills/systemone-gate/SKILL.md`:
```markdown
---
name: systemone-gate
description: Local decision gate using Ollama System One (Nimble & Tev1). Use for local error triage, pre-commit diff risk review, and command safety.
---

When there is a build failure, a test error, or a need to assess the risk of a patch before committing, call the `systemone_triage_error` or `systemone_review_diff` tool.
```

---

## 2. Claude Desktop & Claude Code

Claude Code and Claude Desktop use the standard **Model Context Protocol (MCP)** specification.

Official docs: https://code.claude.com/docs/en/mcp (Claude Code) and https://modelcontextprotocol.io/quickstart/user (Claude Desktop).

### In Claude Desktop (`claude_desktop_config.json`):
The official doc lists only macOS and Windows (Settings > Developer > Edit Config).
* **Linux:** `~/.config/Claude/claude_desktop_config.json` (⚠️ not verified in an official source; the official doc does not list Linux)
* **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
* **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "systemone-gate": {
      "command": "python3",
      "args": ["-m", "systemone_gate.mcp_server"],
      "env": {
        "PYTHONPATH": "/path/to/systemone_gate"
      }
    }
  }
}
```

### In Claude Code (CLI):
Register it through the CLI. The `--` is required to separate the `claude mcp add` options from the server command's flags (such as `-m`):
```bash
claude mcp add systemone-gate -e PYTHONPATH=/path/to/systemone_gate -- python3 -m systemone_gate.mcp_server
```
The default scope is `local` (private, stored in `~/.claude.json`). Use `--scope project` to write to `.mcp.json` at the project root (versionable) or `--scope user` for all projects (`~/.claude.json`). `.mcp.json` has the same `mcpServers` format (`command`/`args`/`env`) as Claude Desktop above. The `.claude/config.json` file is not used for this.

**Cost tip (`systemone_review_staged`):** with the change already in `git add`, call `systemone_review_staged` (no required arguments). The server runs `git diff --cached` in its working directory, reviews per file and returns risk, `coverage` and `decision` (allow/block), without the agent having to paste the diff as an argument to `systemone_review_diff`. The MCP server's working directory must be the repository (set the `cwd` in the client if needed).

---

## 3. Cursor IDE

In Cursor you can expose SystemOne Gate both as a background MCP tool and through context rules (`.cursorrules`).

### Adding it as MCP in Cursor:
Official docs: https://cursor.com/docs/context/mcp

Edit `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project). Menu navigation changes between Cursor versions; the current doc points to the **Customize** panel in the sidebar.

```json
{
  "mcpServers": {
    "systemone": {
      "command": "python3",
      "args": ["-m", "systemone_gate.mcp_server"],
      "env": {
        "PYTHONPATH": "/path/to/systemone_gate"
      }
    }
  }
}
```

### Configuring `.cursorrules` at the project root:
```markdown
# Automatic Gating and Validation Guidelines
Before proposing destructive terminal commands or finishing large architectural changes:
1. The MCP tool `systemone_command_guard` is only a heuristic WARNING (a 0.8B model misses obvious destructive commands); a "safe" verdict authorizes nothing. For commands such as `rm -rf`, `docker prune` or database resets, ask the user for explicit confirmation.
2. On build errors in the terminal, consult `systemone_triage_error` to identify the root cause before applying blind fixes.
```

---

### Real enforcement in Claude Code

`systemone_command_guard` depends on the agent deciding to call it and on the model being right: it is a warning, not a security barrier. Real enforcement must live OUTSIDE the agent. In Claude Code, register the `PreToolUse` hook in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash", "hooks": [{ "type": "command", "command": "systemone-gate hook-guard" }] }
    ]
  }
}
```

`hook-guard` is deterministic, offline and does not call the model: commands that match a rule are blocked (exit 2) and the reason is returned to the agent. An invalid payload does not block, but emits a warning on stderr.

The rules cover only catastrophic, unambiguous patterns (quoted text passed as an argument does not block):
* recursive `rm` on `/`, `~`, `$HOME` or a system directory (`/etc`, `/usr`, `/var`...), or with `--no-preserve-root`;
* `dd of=/dev/sdX`, `mkfs`/`mkswap` on `/dev/*` and redirection `> /dev/sdX`;
* fork bomb;
* `chmod`/`chown -R` on `/` or a system directory;
* `git push --force`/`-f` (or `+main`) to `main`/`master` (`--force-with-lease` does not block);
* `DROP TABLE|DATABASE|SCHEMA`, `TRUNCATE` and `DELETE FROM` without `WHERE`;
* `curl`/`wget` piped to `sh`/`bash`;
* infrastructure, cloud, database and git commands that destroy shared state without an interactive confirmation (for example `terraform destroy -auto-approve`, `kubectl delete namespace production`, `aws s3 rb --force`, `redis-cli FLUSHALL`, `dropdb`, `crontab -r`, `git branch -D main`, `git clean -fx`). The full list, and what is deliberately not blocked, is in [`GUARD_CALIBRATION.md`](GUARD_CALIBRATION.md).

Beyond that, the decision remains human. It is not a sandbox: variables expanded at runtime, scripts and Makefiles are not analyzed.

---

## 4. Windsurf (Codeium Cascade)

> ⚠️ Not verified in an official source in this version of the manual; confirm in the Windsurf documentation (the official docs moved to docs.devin.ai and the page consulted does not mention this path).

Windsurf supports MCP servers through the extensions panel and the `~/.codeium/windsurf/mcp_config.json` file:

```json
{
  "mcpServers": {
    "systemone-gate": {
      "command": "python3",
      "args": ["-m", "systemone_gate.mcp_server"],
      "env": {
        "PYTHONPATH": "/path/to/systemone_gate"
      }
    }
  }
}
```

---

## 5. Cline / Roo Code (VS Code extension)

> ⚠️ Not verified in an official source in this version of the manual; confirm in the Cline and Roo Code documentation (the name `cline_mcp_settings.json` was not confirmed; in Roo the file is `mcp_settings.json` or `.roo/mcp.json`, and the documented auto-approve key is `alwaysAllow`, not `autoApprove`).

Docs: https://docs.cline.bot/mcp/configuring-mcp-servers and https://roocodeinc.github.io/Roo-Code/features/mcp/using-mcp-in-roo

In VS Code with the **Cline** or **Roo Code** extension:
1. Click the MCP icon in the Cline sidebar.
2. Click **Configure MCP Servers** (opens `cline_mcp_settings.json`).
3. Add the configuration:

```json
{
  "mcpServers": {
    "systemone": {
      "command": "python3",
      "args": ["-m", "systemone_gate.mcp_server"],
      "env": {
        "PYTHONPATH": "/path/to/systemone_gate"
      },
      "disabled": false,
      "autoApprove": [
        "systemone_triage_error",
        "systemone_review_diff"
      ]
    }
  }
}
```

---

## 6. Aider (AI pair programming CLI)

Aider creates commits on its own (`auto-commits`, on by default) and, by default, **skips pre-commit hooks** (it uses `--no-verify`). So the supported flow is: install the SystemOne Gate hook (see section 8) and enable the Aider option that makes commits run the hooks.

1. Install the hook at the repository root:

   ```bash
   systemone-gate install-hook
   ```

2. In the `.aider.conf.yml` file at the project root:

   ```yaml
   # Makes Aider's commits run git hooks (default: false, which uses --no-verify)
   git-commit-verify: true
   ```

   Equivalents: the `--git-commit-verify` flag or the `AIDER_GIT_COMMIT_VERIFY=true` variable. Source: https://aider.chat/docs/config/options.html and https://aider.chat/docs/git.html.

Do not use `lint-cmd` with `systemone_gate.cli diff`: Aider appends the names of the edited files to the lint command (the `diff` subcommand does not accept those arguments) and, while Aider is editing, `git diff --cached` is empty because the commit only happens afterwards.

> If the hook blocks (exit != 0), Aider's commit fails; handle the reason shown or adjust the policy (section 7).

---

## 7. Python agents (LangChain, CrewAI, AutoGen, LlamaIndex)

You can import the library directly into your agents' code to build routing nodes and guardrails:

```python
from systemone_gate import PolicyConfig, SystemOneClient, evaluate_command

client = SystemOneClient()
policy = PolicyConfig.from_env()

# 1. Agent routing (which specialist should solve the prompt?)
routing = client.route_task("We need to optimize the locks on the MQTT broker's message queue")
specialist = routing["answers"]["assigned_specialist"]["choice"]
complexity = routing["answers"]["task_complexity"]["score"]

print(f"Specialist: {specialist} | Complexity: {complexity:.2f}")

# 2. Guardrail before running a bash tool
cmd_check = client.guard_command("rm -rf /tmp/mosquitto.db && make clean")
decision = evaluate_command(cmd_check, policy)  # handles Ollama errors and invalid responses
if decision.warning:
    print(f"SystemOne Gate warning: {decision.warning}")
if decision.action == "block":
    raise PermissionError(f"Command blocked by SystemOne Gate: {'; '.join(decision.reasons)}")

# 3. Error triage
triage = client.triage_error("undefined reference to mqtt3_db_open")
root_cause = triage["answers"]["root_cause"]["choice"]
print(f"Root cause identified: {root_cause}")
```

> **Single decision policy:** the CLI, the hook and this snippet use `systemone_gate.policy`. Adjust it through environment variables:
> `SYSTEMONE_GUARD_DANGER_THRESHOLD` (default 1.5), `SYSTEMONE_DIFF_RISK_THRESHOLD` (1.85), `SYSTEMONE_DIFF_BREAKING_THRESHOLD` (0.65),
> `SYSTEMONE_GUARD_ON_ERROR` and `SYSTEMONE_DIFF_ON_ERROR` (`allow` = allow with a warning, default; `block` = block if Ollama fails or answers in the wrong format).
> `SYSTEMONE_MIN_CONFIDENCE` (0 to 1, default `0` = off): with a value > 0, a verdict whose `confidence` reported by the model is below the minimum is treated as indeterminate and follows the surface's `*_ON_ERROR` policy (`allow` = allow with a warning; `block` = block). Verdicts from deterministic rules are never affected; a missing confidence is not penalized.
> These thresholds are **not calibrated** with real data; treat them as starting points.

---

## 8. Git pre-commit hook (universal, for any developer/agent)

If you work in a team or have several agents changing the same repository, make sure no agent commits risky code by installing the hook directly in the repository:

```bash
# At the root of your Git repository:
systemone-gate install-hook
```

From then on, any `git commit` run by you, by Cursor, by Claude Code or by Aider goes automatically through the local model's check.

The hook reviews the diff with `nimble`. Ollama unloads the model after 5 idle minutes and the first call after that takes ≈12 to ≈72 s to load it; the hook uses a 120 s timeout for that reason. To avoid the wait, configure `OLLAMA_KEEP_ALIVE` on the Ollama server (see the "Keep the model loaded" section of the README).
