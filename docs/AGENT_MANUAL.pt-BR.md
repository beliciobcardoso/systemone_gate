# 📘 Manual de Integração do SystemOne Gate para Agentes de IA

🌐 [English](AGENT_MANUAL.md) · **Português (Brasil)**

Este guia orienta como conectar o **SystemOne Gate** a **qualquer agente de Inteligência Artificial** (Claude Code, Antigravity, Cursor, Windsurf, Cline, Roo Code, Aider, LangChain, CrewAI, AutoGen).

---

## 🧠 Por que usar o SystemOne Gate com Agentes de IA?

Agentes de IA generativos (modelos de nuvem como GPT, Claude e Gemini) são excepcionais para raciocínio profundo e geração de código, mas são **lentos e caros** para verificações repetitivas em segundo plano.

O **SystemOne Gate** atua como o sistema reflexo / córtex motor de baixa latência dos agentes:

```mermaid
flowchart LR
    Agent[Agente de IA Generativo] --> |Verificação / Gating| Gate[SystemOne Gate]
    Gate --> |Ollama /v1/systemone| Engine[Nimble 9B / Tev1 0.8B]
    Engine --> |Probabilidades do modelo (baixa latência local; veja README, Desempenho medido)| Gate
    Gate --> |Aprovação / Alerta Estruturado| Agent
```

* **Sem custo de tokens na nuvem:** 100% executado na GPU/CPU local (o custo passa a ser hardware e memória locais).
* **Privacidade total:** Nenhum código ou diff sai da sua máquina.
* **Saída estruturada:** Sem texto prolixo; apenas probabilidades numéricas do modelo (não calibradas; veja o campo `confidence`) e rótulos tipados (`choice` e `score`).

---

## ⚙️ Pré-requisitos e Setup do Ambiente

Antes de configurar qualquer agente, certifique-se de que o backend local do Ollama está instalado e com os modelos prontos:

1. **Instale ou atualize o Ollama para v0.35+**:
   ```bash
   # Linux
   curl -fsSL https://ollama.com/install.sh | sh

   # macOS / Windows
   # Baixe em https://ollama.com/download
   ```
2. **Verifique a versão**:
   ```bash
   ollama -v  # Deve ser >= 0.35.0
   ```
3. **Baixe os modelos necessários**:
   ```bash
   ollama pull tev1:0.8b  # Modelo leve de reflexo rápido (811MB; latência medida no README)
   ollama pull nimble     # Modelo de precisão para código (9B, 9.5GB)
   ```
4. **Instale o pacote localmente**:
   ```bash
   git clone https://github.com/beliciobcardoso/systemone_gate.git
   cd systemone_gate
   pip install -e .
   ```

---

## 1. Antigravity (Google DeepMind)

O Antigravity suporta nativamente servidores MCP e Skills modulares.

Docs oficiais: https://antigravity.google/docs/mcp e https://antigravity.google/docs/skills

### Passo 1: Registrar o Servidor MCP
Edite o arquivo global `~/.gemini/config/mcp_config.json` (ou `.agents/mcp_config.json` no workspace):

```json
{
  "mcpServers": {
    "systemone": {
      "command": "/usr/bin/python3",
      "args": ["-m", "systemone_gate.mcp_server"],
      "env": {
        "PYTHONPATH": "/caminho/para/systemone_gate"
      }
    }
  }
}
```

### Passo 2: Registrar a Skill
Crie `~/.gemini/config/skills/systemone-gate/SKILL.md`:
```markdown
---
name: systemone-gate
description: Local decision gate using Ollama System One (Nimble & Tev1). Use for local error triage, pre-commit diff risk review, and command safety.
---

Quando houver falha de compilação, erro de testes ou necessidade de avaliar o risco de um patch antes do commit, acione a ferramenta `systemone_triage_error` ou `systemone_review_diff`.
```

---

## 2. Claude Desktop & Claude Code

O Claude Code e o Claude Desktop utilizam a especificação padrão do **Model Context Protocol (MCP)**.

Docs oficiais: https://code.claude.com/docs/en/mcp (Claude Code) e https://modelcontextprotocol.io/quickstart/user (Claude Desktop).

### No Claude Desktop (`claude_desktop_config.json`):
A doc oficial lista apenas macOS e Windows (Configurações > Developer > Edit Config).
* **Linux:** `~/.config/Claude/claude_desktop_config.json` (⚠️ não verificado em fonte oficial; a doc oficial não lista Linux)
* **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
* **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "systemone-gate": {
      "command": "python3",
      "args": ["-m", "systemone_gate.mcp_server"],
      "env": {
        "PYTHONPATH": "/caminho/para/systemone_gate"
      }
    }
  }
}
```

### No Claude Code (CLI):
Registre via CLI. O `--` é obrigatório para separar as opções do `claude mcp add` das flags do comando do servidor (como o `-m`):
```bash
claude mcp add systemone-gate -e PYTHONPATH=/caminho/para/systemone_gate -- python3 -m systemone_gate.mcp_server
```
O escopo padrão é `local` (privado, salvo em `~/.claude.json`). Use `--scope project` para gravar em `.mcp.json` na raiz do projeto (versionável) ou `--scope user` para todos os projetos (`~/.claude.json`). O `.mcp.json` tem o mesmo formato `mcpServers` (`command`/`args`/`env`) do Claude Desktop acima. O arquivo `.claude/config.json` não é usado para isso.

**Dica de custo (`systemone_review_staged`):** com a mudança já em `git add`, chame `systemone_review_staged` (sem argumentos obrigatórios). O servidor roda `git diff --cached` no seu diretório de trabalho, revisa por arquivo e retorna risco, `coverage` e `decision` (allow/block), sem que o agente precise colar o diff como argumento do `systemone_review_diff`. O diretório de trabalho do servidor MCP precisa ser o repositório (configure o `cwd` no cliente, se necessário).

---

## 3. Cursor IDE

No Cursor, você pode expor o SystemOne Gate tanto como ferramenta MCP de background quanto através de regras de contexto (`.cursorrules`).

### Adicionando como MCP no Cursor:
Docs oficiais: https://cursor.com/docs/context/mcp

Edite `~/.cursor/mcp.json` (global) ou `.cursor/mcp.json` (projeto). A navegação por menus muda entre versões do Cursor; a doc atual aponta o painel **Customize** na barra lateral.

```json
{
  "mcpServers": {
    "systemone": {
      "command": "python3",
      "args": ["-m", "systemone_gate.mcp_server"],
      "env": {
        "PYTHONPATH": "/caminho/para/systemone_gate"
      }
    }
  }
}
```

### Configurando no `.cursorrules` da raiz do projeto:
```markdown
# Diretrizes de Gating e Validação Automática
Antes de propor comandos destrutivos no terminal ou finalizar alterações grandes de arquitetura:
1. A ferramenta MCP `systemone_command_guard` é apenas um AVISO heurístico (modelo de 0.8B erra comandos destrutivos óbvios); o veredito "safe" não autoriza nada. Para comandos como `rm -rf`, `docker prune` ou resets no banco, peça confirmação explícita ao usuário.
2. Em caso de erros de compilação no terminal, consulte `systemone_triage_error` para identificar a causa raiz antes de tentar aplicar correções cegas.
```

---

### Enforcement real no Claude Code

O `systemone_command_guard` depende do agente decidir chamá-lo e do modelo acertar: é um aviso, não uma barreira de segurança. Enforcement real precisa viver FORA do agente. No Claude Code, registre o hook `PreToolUse` em `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash", "hooks": [{ "type": "command", "command": "systemone-gate hook-guard" }] }
    ]
  }
}
```

O `hook-guard` é determinístico, offline e não chama o modelo: comandos que casam com uma regra são bloqueados (exit 2) e o motivo é devolvido ao agente. Payload inválido não bloqueia, mas emite um aviso no stderr.

As regras cobrem apenas padrões catastróficos e inequívocos (texto entre aspas passado como argumento não bloqueia):
* `rm` recursivo em `/`, `~`, `$HOME` ou diretório de sistema (`/etc`, `/usr`, `/var`...), ou com `--no-preserve-root`;
* `dd of=/dev/sdX`, `mkfs`/`mkswap` em `/dev/*` e redirecionamento `> /dev/sdX`;
* fork bomb;
* `chmod`/`chown -R` em `/` ou diretório de sistema;
* `git push --force`/`-f` (ou `+main`) em `main`/`master` (`--force-with-lease` não bloqueia);
* `DROP TABLE|DATABASE|SCHEMA`, `TRUNCATE` e `DELETE FROM` sem `WHERE`;
* `curl`/`wget` com pipe para `sh`/`bash`;
* comandos de infraestrutura, nuvem, banco e git que destroem estado compartilhado sem confirmação interativa (por exemplo `terraform destroy -auto-approve`, `kubectl delete namespace production`, `aws s3 rb --force`, `redis-cli FLUSHALL`, `dropdb`, `crontab -r`, `git branch -D main`, `git clean -fx`). A lista completa, e o que de propósito não é bloqueado, está em [`GUARD_CALIBRATION.pt-BR.md`](GUARD_CALIBRATION.pt-BR.md).

Fora disso, a decisão continua sendo humana. Não é um sandbox: variáveis expandidas em runtime, scripts e Makefiles não são analisados.

### Caminhos protegidos

As regras embutidas só conhecem catástrofes genéricas. Para tornar seus próprios diretórios intocáveis, defina `SYSTEMONE_PROTECTED_PATHS` com uma lista de caminhos absolutos separados por `:` (`~`, `$HOME` e `${HOME}` são expandidos):

```json
{ "env": { "SYSTEMONE_PROTECTED_PATHS": "~/Projetos:~/.ssh:/srv/dados" } }
```

Coloque no `env` do `settings.json` do Claude Code: a variável precisa chegar ao processo do `hook-guard` (um prefixo no comando do próprio agente não chega, então o agente não consegue desligá-la assim). `guard` e o servidor MCP leem do ambiente deles.

* **Bloqueado:** `rm`, `mv` (a origem), `shred` e `chmod`/`chown`/`chgrp` recursivo no próprio caminho, no conteúdo (`<caminho>/*`) e, em operações recursivas e `mv`, em qualquer ancestral (`rm -rf /home/eu` destrói `~/Projetos`). Globs que podem casar com um caminho protegido (`rm -rf ~/Proj*`) também bloqueiam. O id da regra é `protected-path`.
* **Livre:** qualquer coisa mais fundo (`rm -rf ~/Projetos/x/build`), nomes parecidos (`~/Projetos2`) e escrever dentro do caminho (`mv a ~/Projetos`, `cp`).
* Alvos relativos resolvem contra o `cwd` do payload do hook (ou o diretório de trabalho do processo, em `guard`/MCP).
* Entrada inválida (não absoluta, `$OUTRA`, `~usuario`) é ignorada com aviso no stderr; nunca bloqueia.
* Limites, como nas demais regras: só análise de texto. Symlinks não são resolvidos, e variáveis, scripts, `find -delete` e redirecionamentos não são analisados.

---

## 4. Windsurf (Codeium Cascade)

> ⚠️ Não verificado em fonte oficial nesta versão do manual; confirme na documentação do Windsurf (a doc oficial foi migrada para docs.devin.ai e a página consultada não cita este caminho).

O Windsurf suporta servidores MCP através do painel de extensões e do arquivo de configuração `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "systemone-gate": {
      "command": "python3",
      "args": ["-m", "systemone_gate.mcp_server"],
      "env": {
        "PYTHONPATH": "/caminho/para/systemone_gate"
      }
    }
  }
}
```

---

## 5. Cline / Roo Code (VS Code Extension)

> ⚠️ Não verificado em fonte oficial nesta versão do manual; confirme na documentação do Cline e do Roo Code (o nome `cline_mcp_settings.json` não foi confirmado; no Roo o arquivo é `mcp_settings.json` ou `.roo/mcp.json`, e a chave de aprovação automática documentada é `alwaysAllow`, não `autoApprove`).

Docs: https://docs.cline.bot/mcp/configuring-mcp-servers e https://roocodeinc.github.io/Roo-Code/features/mcp/using-mcp-in-roo

No VS Code com a extensão **Cline** ou **Roo Code**:
1. Clique no ícone de MCP na barra lateral do Cline.
2. Clique em **Configure MCP Servers** (abre `cline_mcp_settings.json`).
3. Adicione a configuração:

```json
{
  "mcpServers": {
    "systemone": {
      "command": "python3",
      "args": ["-m", "systemone_gate.mcp_server"],
      "env": {
        "PYTHONPATH": "/caminho/para/systemone_gate"
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

## 6. Aider (AI Pair Programming CLI)

O Aider cria os commits por conta própria (`auto-commits`, padrão ligado) e, por padrão, **pula os hooks de pré-commit** (usa `--no-verify`). Por isso o fluxo suportado é: instalar o hook do SystemOne Gate (veja a seção 8) e habilitar no Aider a opção que faz os commits rodarem os hooks.

1. Instale o hook na raiz do repositório:

   ```bash
   systemone-gate install-hook
   ```

2. No arquivo `.aider.conf.yml` na raiz do projeto:

   ```yaml
   # Faz os commits do Aider executarem os hooks git (padrão: false, que usa --no-verify)
   git-commit-verify: true
   ```

   Equivalentes: flag `--git-commit-verify` ou variável `AIDER_GIT_COMMIT_VERIFY=true`. Fonte: https://aider.chat/docs/config/options.html e https://aider.chat/docs/git.html.

Não use `lint-cmd` com `systemone_gate.cli diff`: o Aider anexa os nomes dos arquivos editados ao comando de lint (o subcomando `diff` não aceita esses argumentos) e, enquanto o Aider edita, `git diff --cached` está vazio porque o commit só acontece depois.

> Se o hook bloquear (exit != 0), o commit do Aider falha; trate o motivo exibido ou ajuste a política (seção 7).

---

## 7. Agentes em Python (LangChain, CrewAI, AutoGen, LlamaIndex)

Você pode importar a biblioteca diretamente dentro do código dos seus agentes para criar nós de roteamento e guardrails:

```python
from systemone_gate import PolicyConfig, SystemOneClient, evaluate_command

client = SystemOneClient()
policy = PolicyConfig.from_env()

# 1. Roteamento de agente (qual especialista deve resolver o prompt?)
routing = client.route_task("Precisamos otimizar os locks na fila de mensagens do broker MQTT")
specialist = routing["answers"]["assigned_specialist"]["choice"]
complexity = routing["answers"]["task_complexity"]["score"]

print(f"Especialista: {specialist} | Complexidade: {complexity:.2f}")

# 2. Guardrail antes de rodar tool de bash
cmd_check = client.guard_command("rm -rf /tmp/mosquitto.db && make clean")
decision = evaluate_command(cmd_check, policy)  # trata erro do Ollama e resposta inválida
if decision.warning:
    print(f"Aviso do SystemOne Gate: {decision.warning}")
if decision.action == "block":
    raise PermissionError(f"Comando bloqueado pelo SystemOne Gate: {'; '.join(decision.reasons)}")

# 3. Triagem de erro
triage = client.triage_error("undefined reference to mqtt3_db_open")
root_cause = triage["answers"]["root_cause"]["choice"]
print(f"Causa-raiz identificada: {root_cause}")
```

> **Política de decisão única:** CLI, hook e este snippet usam `systemone_gate.policy`. Ajuste via variáveis de ambiente:
> `SYSTEMONE_GUARD_DANGER_THRESHOLD` (padrão 1.5), `SYSTEMONE_DIFF_RISK_THRESHOLD` (1.85), `SYSTEMONE_DIFF_BREAKING_THRESHOLD` (0.65),
> `SYSTEMONE_GUARD_ON_ERROR` e `SYSTEMONE_DIFF_ON_ERROR` (`allow` = libera com aviso, padrão; `block` = bloqueia se o Ollama falhar ou responder fora do formato).
> `SYSTEMONE_MIN_CONFIDENCE` (0 a 1, padrão `0` = desligado): com valor > 0, um veredito cuja `confiança` informada pelo modelo seja menor que o mínimo é tratado como indeterminado e segue a política `*_ON_ERROR` da superfície (`allow` = libera com aviso; `block` = bloqueia). Vereditos de regras determinísticas nunca são afetados; confiança ausente não penaliza.
> Esses limiares **não são calibrados** com dados reais; trate-os como pontos de partida.

---

## 8. Git Pre-Commit Hook (Universal para Qualquer Desenvolvedor/Agente)

Se você trabalha em equipe ou tem múltiplos agentes alterando o mesmo repositório, garanta que nenhum agente commite código arriscado instalando o hook diretamente no repositório:

```bash
# Na raiz do seu repositório Git:
systemone-gate install-hook
```

A partir desse momento, qualquer `git commit` executado por você, pelo Cursor, pelo Claude Code ou pelo Aider passará automaticamente pelo crivo do modelo local.

O hook revisa o diff com o `nimble`. O Ollama descarrega o modelo após 5 minutos parado e a primeira chamada depois disso leva de ≈12 a ≈72 s para carregá-lo; o hook usa timeout de 120 s por isso. Para evitar a espera, configure `OLLAMA_KEEP_ALIVE` no servidor Ollama (veja a seção "Manter o modelo carregado" do README).
