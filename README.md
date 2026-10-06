# 🛡️ SystemOne Gate

🌐 **English** · [Português (Brasil)](README.pt-BR.md)

> **A local decision engine with no cloud token cost, for AI agents and software developers.**  
> Based on the **System One** architecture (TypeSafe AI's JEV style), run 100% offline through **Ollama 0.35+**.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.9+](https://img.shields.io/badge/Python-3.9+-brightgreen.svg)](pyproject.toml)
[![Protocol: MCP](https://img.shields.io/badge/Protocol-MCP%202024--11--05-orange.svg)](https://modelcontextprotocol.io)
[![Backend: Ollama](https://img.shields.io/badge/Ollama-0.35+-black.svg)](https://ollama.com)

---

## ⚡ What is SystemOne Gate?

Generative agents such as Claude, Gemini and GPT-4 produce long text token by token (*System Two thinking*). **SystemOne Gate** acts as the reflex system (*System One thinking*) of the AI ecosystem.

It evaluates structured input in parallel while generating only **1 to 3 output tokens**, together with the model's probabilities (not calibrated; see the `confidence` field), for critical decisions:

* 🩺 **Error triage:** classifies whether a failure is a compile error, syntax error, linker error, memory leak or timeout.
* 🔍 **Diff code review:** estimates the probability of a breaking change and of architectural risk before each commit.
* 🛡️ **Shell command guardrail:** evaluates whether a terminal command could delete data or break the environment, with low local latency (see [Measured performance](#-measured-performance)).
* 🔀 **Subagent routing:** decides which subagent a development task should be sent to.

---

## 📊 Supported Models (via Ollama)

| Model | Size | Provider | Latency | Ideal use |
| :--- | :--- | :--- | :--- | :--- |
| **`nimble`** | 9.5 GB (9B) | Bespoke Labs | ~390-410 ms (measured, see below) | Default for diff review (pre-commit), deep code review, breaking-change detection and complex error triage. |
| **`tev1:0.8b`** | 811 MB (0.8B) | Together AI | ~145-165 ms (measured, see below) | Shell command guardrail with low local latency. **It does not discriminate the risk of a diff** (see the pre-commit hook paragraph). |
| **`tev1:4b`** | ~2.5 GB (4B) | Together AI | not measured | Middle ground between speed and accuracy. |


---

## 🧪 Measured performance

Measured on 2026-10-05 with `SystemOneClient` (sequential calls, local Ollama, RTX 3060 12 GB GPU, 100% GPU, 28-thread CPU, idle machine). Procedure: 2 discarded warm-up calls + 30 timed calls (end-to-end latency, including HTTP and JSON). Reproduce with `python benchmarks/latency.py --cold`.

| Model | Payload | p50 | p95 | min / max | Cold start* |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `tev1:0.8b` | guard rubric, short command | 162.6 ms | 170.1 ms | 150.2 / 170.8 ms | ~3-4 s |
| `tev1:0.8b` | diff-risk rubric, ~100-line diff | 143.2 ms | 160.5 ms | 133.3 / 166.2 ms | - |
| `nimble:latest` | guard rubric, short command | 391.6 ms | 401.2 ms | 366.3 / 409.0 ms | ~12-72 s |
| `nimble:latest` | diff-risk rubric, ~100-line diff | 405.1 ms | 423.7 ms | 377.1 / 428.7 ms | - |

\* First call after `ollama stop <model>` (model unloaded from memory). The `nimble` range comes from two independent measurements that diverged (≈11.8 s with the machine idle, ≈46.5 s in a run with concurrent load on Ollama and ≈72 s on the first call of the rubric benchmark); the real value varies with the system disk cache and with machine load. In some of those cases the default `SYSTEMONE_TIMEOUT` of 30 s is not enough for the first `nimble` call.

* These numbers were measured with the rubrics still in Portuguese. The switch to English (2026-10-06) changes the prompt size and **has not been re-measured**; run `python benchmarks/latency.py` to update.
* Latency depends on hardware, on whether the model is already resident in memory, and on payload size; do not extrapolate these numbers to another machine. The test diff uses short lines because the endpoint rejects inputs above ~2050 tokens.
* The **deterministic rules** layer (`guard_rules.evaluate_command`, offline, no model) is the fast path: ~40-50 µs per call (1000 calls, same machine). The model verdict is an additional heuristic, not the security barrier: in the calibration (84 commands, two labelers) no threshold of `tev1:0.8b` or `nimble` met recall >= 90% with FPR <= 5%, so the defaults were left unchanged and the rules were widened instead (see [`docs/GUARD_CALIBRATION.md`](docs/GUARD_CALIBRATION.md)).
* Reproducibility: 20 identical calls to `tev1:0.8b` and 20 to `nimble:latest` (guard rubric) returned identical answers, including the probabilities. This was observed on this machine and Ollama version; it is not a vendor-documented guarantee.

---

### Keep the model loaded (`OLLAMA_KEEP_ALIVE`)

Ollama unloads a model after a period of inactivity; **the default is 5 minutes**. The first call after that pays the load cost (measured: ≈12 to ≈72 s for `nimble`, ≈3-4 s for `tev1:0.8b`), which is why the pre-commit hook, which uses `nimble`, has a 120 s timeout. Keeping the model loaded avoids that cost.

**See what is loaded:** `ollama ps` shows the model, its size, the processor and, in the `UNTIL` column, when it will be unloaded.

**How to configure it:** this is a setting of the Ollama **server** (it applies to all clients). The `OLLAMA_KEEP_ALIVE` variable accepts:

| Value | Effect |
| :--- | :--- |
| `30m`, `24h` | keeps the model loaded for that long after last use |
| `3600` | a number of seconds |
| `-1` | keeps it loaded **indefinitely** |
| `0` | unloads right after the response |

On Linux, with the systemd service installed by the official installer:

```bash
sudo systemctl edit ollama.service
# in the editor, add:
#   [Service]
#   Environment="OLLAMA_KEEP_ALIVE=30m"
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

Without systemd, set the variable when starting the server: `OLLAMA_KEEP_ALIVE=30m ollama serve`. For macOS and Windows, follow the [official Ollama FAQ](https://docs.ollama.com/faq). Restarting the service unloads whatever was in memory.

**Cost:** the model occupies memory while it is loaded. On the test machine (12 GB RTX 3060) `nimble` used 8.9 GB, according to `ollama ps`. Prefer a finite value that covers a work session (for example `30m` to `1h`); use `-1` only on a dedicated machine, because then memory is only released by restarting the service or with `ollama stop <model>`.

**What SystemOne Gate does not do:** it does **not** set `keep_alive` per request. Ollama's documentation lists that parameter for `/api/generate` and `/api/chat`, not for `/v1/systemone`, and it has not been verified that this endpoint accepts it.

## 🚀 Quick Start (From Zero to Working)

If you are new to the project, follow these steps to set up Ollama and SystemOne Gate on your machine.

### Step 1: Install or update Ollama (version 0.35+)

The `/v1/systemone` endpoint is a recent feature introduced in **Ollama v0.35.0**. Make sure you are on version 0.35 or later.

* **Linux:**
  ```bash
  curl -fsSL https://ollama.com/install.sh | sh
  ```
* **macOS / Windows:**
  Download the latest installer from [ollama.com/download](https://ollama.com/download).

**Check the installed version:**
```bash
ollama -v
# Should print: ollama version is 0.35.0 (or higher)
```

---

### Step 2: Pull the decision models (System One)

SystemOne Gate uses models trained specifically for classification, scores and parallel decisions (they are not free-text chatbots):

```bash
# 1. Tev1 (0.8B) - light and fast (811 MB download)
# Fits any machine, low latency (see Measured performance). Useful for shell command checks.
ollama pull tev1:0.8b

# 2. Nimble (9B) - high accuracy for code (9.5 GB download)
# Recommended for GPUs with 8GB+ VRAM or Apple Silicon. Used for bug triage and code review.
ollama pull nimble
```

> 💡 **Hardware note:** on a more modest machine (no dedicated GPU or little VRAM) you can use only `tev1:0.8b` for every task without problems!

---

### Step 3: Sanity check (is the API up?)

With Ollama running in the background, make a quick test call from the terminal:

```bash
curl http://localhost:11434/v1/systemone -d '{
  "model": "tev1:0.8b",
  "state": "Build error: undefined reference to main",
  "questions": {
    "is_linker_error": {
      "type": "choice",
      "instructions": "Is this a linker error?",
      "criteria": {"yes": null, "no": null}
    }
  }
}'
```

If it returns JSON with `"choice": "yes"` and `"probabilities"`, the backend is ready!

---

### Step 4: Install SystemOne Gate

Clone this repository and install the CLI:

```bash
git clone https://github.com/beliciobcardoso/systemone_gate.git
cd systemone_gate
pip install -e .
```

Done! The `systemone-gate` command and the `systemone-mcp` server are now available in your terminal.

---

## 🛠️ Usage

### 1. Command line (CLI)

```bash
# Inspect staged changes before committing
systemone-gate diff

# Inspect the diff with the Nimble model (deeper analysis)
systemone-gate diff --nimble

# Any Ollama model (order: --model/--nimble > SYSTEMONE_DIFF_MODEL > nimble)
systemone-gate diff --model NAME

# Triage a build or test error
systemone-gate triage "undefined reference to mqtt3_db_open in mosquitto.c"

# Check whether a terminal command is safe
systemone-gate guard "rm -rf /tmp/data/*"

# Show the installed version
systemone-gate --version

# Install the Git pre-commit hook in the current repository
systemone-gate install-hook

# Remove the hook (restores the original hook, if there is a backup)
systemone-gate uninstall-hook

# Diagnose the Ollama backend (version, models and endpoint contract)
systemone-gate doctor
```

**Diagnostics (`doctor`):** SystemOne Gate depends on a third-party endpoint with no versioned contract, so this is the quick way to investigate errors such as "Failed to connect" or HTTP 404. The command checks, in order: (1) whether Ollama answers on `/api/version`; (2) whether the version is **>= 0.35.0** (the required minimum; before that `/v1/systemone` does not exist); (3) whether the `tev1:0.8b` and `nimble` models are installed and have the `decision` capability (use `--model NAME`, repeatable, to change the list); (4) a contract test with one minimal call to `/v1/systemone` (skip it with `--no-smoke`). It prints a checklist (✅/⚠️/❌) and exits 0 if everything required passed, 1 if something failed and 2 for invalid configuration (e.g. `SYSTEMONE_TIMEOUT`).

**Pre-commit hook model:** the hook reviews the diff with `nimble` by default. In the rubric benchmark ([`docs/BENCHMARK_RUBRIC_LANGUAGE.md`](docs/BENCHMARK_RUBRIC_LANGUAGE.md); 36 diffs labeled by an LLM, one machine; this is not calibration) `tev1:0.8b` **did not discriminate diff risk** (it got 36-39% of the risk level right, against 33% by chance, and 33-53% of `breaking_change`, against 56% for always answering `safe`), while `nimble` got 72-75% of the risk and 69% of `breaking_change`. **Cost:** Ollama unloads the model after it sits idle (Ollama default: 5 minutes; see [Keep the model loaded](#keep-the-model-loaded-ollama_keep_alive)), and the first call after that takes ≈12 to ≈72 s to load `nimble`. That is why the hook uses a **120 s timeout** by default (the CLI and the library stay at 30 s); set `SYSTEMONE_TIMEOUT` to change it. To go back to the fast model: `SYSTEMONE_DIFF_MODEL=tev1:0.8b git commit ...`. **Hooks already installed** only get the 120 s timeout if reinstalled (`systemone-gate install-hook`); without that, the first commit after a pause can exceed 30 s and the review is skipped with a warning.

**Skipping the hook:** `SYSTEMONE_SKIP=1 git commit ...` skips only the SystemOne Gate check (the other hooks still run). Avoid `git commit --no-verify`, which disables all hooks.

**Emoji-free output:** if the terminal or pipe does not support UTF-8 (e.g. `PYTHONIOENCODING=ascii`), the CLI automatically swaps emoji for ASCII tokens (`[OK]`, `[ERROR]`, `[WARN]`). To force this mode, use `systemone-gate --plain diff` or `SYSTEMONE_PLAIN=1`. The MCP server is not affected.

**Timeout:** the default is 30 s per call. On first use after startup Ollama loads the model into memory (Nimble is 9.5 GB) and may take longer; raise it with `SYSTEMONE_TIMEOUT` (seconds, positive number), for example `SYSTEMONE_TIMEOUT=120 systemone-gate triage "..."`. An invalid value makes the CLI exit with code 2. The endpoint can be changed with `OLLAMA_SYSTEMONE_URL`.

**Minimum confidence (opt-in):** `SYSTEMONE_MIN_CONFIDENCE` (0 to 1) treats a verdict whose `confidence` is below the minimum as indeterminate and applies the `*_ON_ERROR` policy; it is off (`0`) and has no suggested value because there is no calibration and the observed confidences are low across all answers (0.03 to 0.27), so any high minimum would always block or warn.

---

### Rubric profiles (`diff` and `triage`)

The diff-risk and error-triage rubrics have profiles because the original text is aimed at C/systems code (sockets, locks, protocol parsing). The text of every rubric sent to the model is in **English** (public, international project; see [`docs/BENCHMARK_RUBRIC_LANGUAGE.md`](docs/BENCHMARK_RUBRIC_LANGUAGE.md)); the choice keys, which the policy reads, do not change:

| Profile | Purpose |
|---|---|
| `default` | Original text (C/systems), kept for compatibility. |
| `generic` | Neutral wording, without C/network jargon. |
| `web-backend` | NestJS/Prisma/PostgreSQL/Java Spring services: destructive migrations, authentication/authorization, queries without a tenant filter, REST/GraphQL contracts, transactions, secrets. |

Selection: `systemone-gate diff --profile web-backend`, `systemone-gate triage --profile web-backend "error"` or the `SYSTEMONE_PROFILE` variable (the argument takes precedence). An invalid profile exits with code 2. In MCP and the library the environment variable applies, or `profile=` in `review_diff`/`triage_error`. The `guard` and routing rubrics do not change.

> **Warning:** the `generic` and `web-backend` profiles have **not yet been validated** against labeled data; the quality of the new wording has not been measured. `default` remains the original C/systems text.

### 2. As an MCP server (Model Context Protocol)

SystemOne Gate ships a native, zero-dependency MCP server compatible with:
* **Antigravity (Google DeepMind)**
* **Claude Desktop & Claude Code**
* **Cursor IDE**
* **Windsurf (Cascade)**
* **Cline & Roo Code (VS Code)**

#### MCP configuration example (`mcp.json` / `claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "systemone": {
      "command": "systemone-mcp"
    }
  }
}
```

#### Exposed MCP tools:
1. `systemone_triage_error`: triage and root cause of build or test failures.
2. `systemone_review_diff`: technical risk and contract-break assessment for code patches.
3. `systemone_command_guard`: safety check for bash commands (light Tev1 0.8B model; see Measured performance).
4. `systemone_query`: arbitrary typed queries (`choice` or `score`) for any context.
5. `systemone_review_staged`: reviews what is staged (`git diff --cached` read by the server itself, per file, ignoring lockfiles/binaries) and returns risk, coverage and the allow/block decision. Preferable to `systemone_review_diff` when the change is already staged: the diff does not pass through the agent (fewer output tokens and no risk of summarizing or altering it).

---

### 3. As a Python library (for custom agents)

```python
from systemone_gate import SystemOneClient

client = SystemOneClient()

# Error triage
triage = client.triage_error("mosquitto.c:120: segmentation fault (core dumped)")
print("Root cause:", triage["answers"]["root_cause"]["choice"])

# Risk assessment of a patch
diff = "--- a/net.c\n+++ b/net.c\n@@ -10 +10 @@\n- socket_read();\n+ async_epoll_wait();"
review = client.review_diff(diff)
print("Risk (0 to 2):", review["answers"]["risk_level"]["score"])
```

---

## 📖 Manuals and Integration Guides

For step-by-step guides on plugging SystemOne Gate into each specific agent, see:
* 📘 [**Complete Manual for AI Agents**](docs/AGENT_MANUAL.md) (Claude, Cursor, Windsurf, Cline, Aider, Antigravity, LangChain)
* 💡 [Python agent script example](examples/python_agent_integration.py)
* 📋 [Configuration for Cursor IDE](examples/cursor_rules.md)

---

## 🔒 Privacy and Security

* **100% local:** all processing happens inside the developer's machine (`localhost:11434`). The endpoint must use `http`/`https` and point to a loopback host (`localhost`, `127.0.0.0/8`, `::1`, `*.localhost`); remote hosts are only accepted with `SYSTEMONE_ALLOW_REMOTE=1` (a warning is printed to stderr, since diffs, commands and logs will leave the machine). No hostname is resolved through DNS: anything other than `localhost`/`*.localhost` counts as remote.
* **Secret redaction (on by default):** before sending to the model, the text (`state`: diffs, commands, logs) goes through `redact_secrets`, which replaces AWS keys, GitHub/Slack/Stripe tokens, Google keys, PEM private-key blocks, JWTs, `Authorization: Bearer ...`, passwords in URLs and `password|secret|api_key|token=...` assignments with `[REDACTED:<rule>]`. When something is masked, the result carries `"redacted": <n>`. Disable it with `SYSTEMONE_REDACT=0` or `SystemOneClient(redact=False)`. This is pattern-based risk reduction, **not a guarantee**: unrecognized formats pass through. The deterministic `guard_command` rules see the original command, without redaction.
* **No telemetry:** SystemOne Gate does not collect or send data to the cloud.
* **Resilient to network failures:** if the local Ollama service is down, the pre-commit hook lets the normal development flow proceed so it never blocks the user.

---

## 🛠️ Development

```bash
pip install -e ".[dev]"   # pytest, ruff and mypy
scripts/check.sh          # ruff check → ruff format --check → mypy → pytest
```

- **ruff** (`E,F,W,I,B`): style, unused/unsorted imports and common pitfalls (bugbear); it also formats. `format --check` only covers **new** Python files (existing ones were not mass-reformatted); `FORMAT_ALL=1 scripts/check.sh` checks everything.
- **mypy**: type checking of `systemone_gate/` (tests are not typed).
- **pytest**: hermetic suite (it does not call the real Ollama; the `contract` test is opt-in).
- There is no CI on purpose: the checks are local.

---

## 📄 License

Distributed under the [MIT](LICENSE) license.
