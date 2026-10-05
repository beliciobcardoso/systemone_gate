# 🛡️ SystemOne Gate

> **Motor de Decisão Ultrarrápido, Local e com Custo Zero para Agentes de IA e Desenvolvedores de Software.**  
> Baseado na arquitetura **System One** (estilo JEV da TypeSafe AI), executado 100% offline via **Ollama 0.35+**.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.9+](https://img.shields.io/badge/Python-3.9+-brightgreen.svg)](pyproject.toml)
[![Protocol: MCP](https://img.shields.io/badge/Protocol-MCP%202024--11--05-orange.svg)](https://modelcontextprotocol.io)
[![Backend: Ollama](https://img.shields.io/badge/Ollama-0.35+-black.svg)](https://ollama.com)

---

## ⚡ O que é o SystemOne Gate?

Enquanto agentes generativos como Claude, Gemini e GPT-4 geram textos extensos token a token (*System Two thinking*), o **SystemOne Gate** atua como o sistema reflexo (*System One thinking*) do ecossistema de inteligência artificial.

Ele avalia dados estruturados em paralelo gerando apenas **1 a 3 tokens de saída** com probabilidades calibradas para decisões críticas:

* 🩺 **Triagem de Erros:** Identifica instantaneamente se uma falha é de compilação, sintaxe, linkedição, memory leak ou timeout.
* 🔍 **Code Review de Diffs:** Mede a probabilidade de breaking change e risco arquitetural antes de cada commit.
* 🛡️ **Guardrail de Comandos Shell:** Avalia se um comando de terminal pode apagar dados ou quebrar o ambiente em menos de 15 milissegundos.
* 🔀 **Roteamento de Subagentes:** Decide para qual subagente encaminhar uma tarefa de desenvolvimento.

---

## 📊 Modelos Suportados (via Ollama)

| Modelo | Tamanho | Provedor | Latência Típica | Caso de Uso Ideal |
| :--- | :--- | :--- | :--- | :--- |
| **`nimble`** | 9.5 GB (9B) | Bespoke Labs | ~80ms - 200ms | Code review profundo, detecção de breaking changes e triagem de erros complexos. |
| **`tev1:0.8b`** | 811 MB (0.8B) | Together AI | **< 15ms** | Guardrail de comandos shell em tempo real e Git pre-commit hooks ultra-rápidos. |
| **`tev1:4b`** | ~2.5 GB (4B) | Together AI | ~40ms | Equilíbrio intermediário entre velocidade e precisão. |

---

## 🚀 Guia de Início Rápido (Do Zero ao Funcionamento)

Se você está chegando agora ao projeto, siga este passo a passo para configurar o Ollama e o SystemOne Gate na sua máquina.

### Passo 1: Instalar ou Atualizar o Ollama (Versão 0.35+)

O endpoint `/v1/systemone` é uma funcionalidade recente introduzida no **Ollama v0.35.0**. Certifique-se de estar com a versão 0.35 ou superior.

* **Linux:**
  ```bash
  curl -fsSL https://ollama.com/install.sh | sh
  ```
* **macOS / Windows:**
  Baixe o instalador mais recente em [ollama.com/download](https://ollama.com/download).

**Verifique a versão instalada:**
```bash
ollama -v
# Deve exibir: ollama version is 0.35.0 (ou superior)
```

---

### Passo 2: Baixar os Modelos de Decisão (System One)

O SystemOne Gate utiliza modelos treinados especificamente para classificação, scores e decisões paralelas (não são chatbots de texto livre):

```bash
# 1. Tev1 (0.8B) - Ultra-rápido (811 MB de download)
# Ideal para qualquer máquina, latência < 15ms. Essencial para checagem de comandos shell.
ollama pull tev1:0.8b

# 2. Nimble (9B) - Alta precisão para código (9.5 GB de download)
# Recomendado para GPUs com 8GB+ VRAM ou Apple Silicon. Usado em triagem de bugs e code review.
ollama pull nimble
```

> 💡 **Nota de Hardware:** Se você estiver em uma máquina mais modesta (sem GPU dedicada ou com pouca VRAM), você pode usar apenas o `tev1:0.8b` para todas as tarefas sem problemas!

---

### Passo 3: Teste de Sanidade (Verificar se a API está ativa)

Com o Ollama rodando em background, faça uma chamada de teste rápida no terminal:

```bash
curl http://localhost:11434/v1/systemone -d '{
  "model": "tev1:0.8b",
  "state": "Erro ao compilar: undefined reference to main",
  "questions": {
    "is_linker_error": {
      "type": "choice",
      "instructions": "Este é um erro de linkedição?",
      "criteria": {"yes": null, "no": null}
    }
  }
}'
```

Se retornar um JSON com `"choice": "yes"` e `"probabilities"`, o backend está 100% pronto!

---

### Passo 4: Instalar o SystemOne Gate

Clone este repositório e instale a CLI:

```bash
git clone https://github.com/beliciobcardoso/systemone_gate.git
cd systemone_gate
pip install -e .
```

Pronto! Agora o comando `systemone-gate` e o servidor `systemone-mcp` estão disponíveis no seu terminal.

---

## 🛠️ Modos de Uso

### 1. Linha de Comando (CLI)

```bash
# Inspecionar alterações staged antes do commit
systemone-gate diff

# Inspecionar diff com o modelo Nimble (análise mais profunda)
systemone-gate diff --nimble

# Qualquer modelo Ollama (ordem: --model/--nimble > SYSTEMONE_DIFF_MODEL > tev1:0.8b)
systemone-gate diff --model NOME

# Triagem de erro de build ou teste
systemone-gate triage "undefined reference to mqtt3_db_open no mosquitto.c"

# Testar se um comando de terminal é seguro
systemone-gate guard "rm -rf /tmp/data/*"

# Instalar Git Pre-Commit Hook no repositório atual
systemone-gate install-hook

# Remover o hook (restaura o hook original, se houver backup)
systemone-gate uninstall-hook
```

**Modelo do pre-commit hook:** o hook usa `tev1:0.8b` por padrão (rápido, porém menos preciso). Esse padrão **não foi calibrado nem validado por benchmark**. Para usar o Nimble no hook, rode `SYSTEMONE_DIFF_MODEL=nimble git commit ...` ou exporte `SYSTEMONE_DIFF_MODEL=nimble` no shell.

**Ignorar o hook:** `SYSTEMONE_SKIP=1 git commit ...` pula apenas a verificação do SystemOne Gate (os demais hooks continuam valendo). Evite `git commit --no-verify`, que desativa todos os hooks.

**Saída sem emoji:** se o terminal ou o pipe não suporta UTF-8 (ex.: `PYTHONIOENCODING=ascii`), a CLI troca os emoji por tokens ASCII (`[OK]`, `[ERRO]`, `[AVISO]`) automaticamente. Para forçar esse modo, use `systemone-gate --plain diff` ou `SYSTEMONE_PLAIN=1`. O servidor MCP não é afetado.

**Timeout:** o padrão é 30 s por chamada. No primeiro uso após inicialização a Ollama carrega o modelo em memória (o Nimble tem 9,5 GB) e pode demorar mais; aumente com `SYSTEMONE_TIMEOUT` (segundos, número positivo), por exemplo `SYSTEMONE_TIMEOUT=120 systemone-gate triage "..."`. Um valor inválido encerra a CLI com código 2. O endpoint pode ser trocado com `OLLAMA_SYSTEMONE_URL`.

---

### 2. Como Servidor MCP (Model Context Protocol)

O SystemOne Gate possui um servidor MCP nativo sem dependências externas (Zero-Dependency) compatível com:
* **Antigravity (Google DeepMind)**
* **Claude Desktop & Claude Code**
* **Cursor IDE**
* **Windsurf (Cascade)**
* **Cline & Roo Code (VS Code)**

#### Exemplo de Configuração MCP (`mcp.json` / `claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "systemone": {
      "command": "systemone-mcp"
    }
  }
}
```

#### Ferramentas MCP Expostas:
1. `systemone_triage_error`: Triagem e causa-raiz de falhas de compilação ou testes.
2. `systemone_review_diff`: Avaliação de risco técnico e quebras de contrato em patches de código.
3. `systemone_command_guard`: Verificação de segurança de comandos bash (<15ms via Tev1 0.8B).
4. `systemone_query`: Consultas arbitrárias tipadas (`choice` ou `score`) para qualquer contexto.

---

### 3. Como Biblioteca Python (Para Agentes Customizados)

```python
from systemone_gate import SystemOneClient

client = SystemOneClient()

# Triagem de erro
triage = client.triage_error("mosquitto.c:120: segmentation fault (core dumped)")
print("Causa-raiz:", triage["answers"]["root_cause"]["choice"])

# Avaliação de risco em patch
diff = "--- a/net.c\n+++ b/net.c\n@@ -10 +10 @@\n- socket_read();\n+ async_epoll_wait();"
review = client.review_diff(diff)
print("Risco (0 a 2):", review["answers"]["risk_level"]["score"])
```

---

## 📖 Manuais e Guias de Integração

Para guias passo a passo de como plugar o SystemOne Gate em cada agente específico, consulte:
* 📘 [**Manual Completo para Agentes de IA**](docs/MANUAL_AGENTES_IA.md) (Claude, Cursor, Windsurf, Cline, Aider, Antigravity, LangChain)
* 💡 [Exemplo de Script em Python para Agentes](examples/python_agent_integration.py)
* 📋 [Configuração para Cursor IDE](examples/cursor_rules.md)

---

## 🔒 Privacidade e Segurança

* **100% Local:** Todo o processamento acontece dentro da máquina do desenvolvedor (`localhost:11434`).
* **Sem Telemetria:** O SystemOne Gate não coleta e não envia dados para a nuvem.
* **Resiliente a Falhas de Rede:** Se o serviço local do Ollama estiver inativo, o pre-commit hook permite o fluxo normal de desenvolvimento para nunca bloquear o usuário.

---

## 📄 Licença

Distribuído sob a licença [MIT](LICENSE).
