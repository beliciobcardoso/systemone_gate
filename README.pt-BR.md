# 🛡️ SystemOne Gate

🌐 [English](README.md) · **Português (Brasil)**

> **Motor de Decisão Local, sem Custo de Tokens na Nuvem, para Agentes de IA e Desenvolvedores de Software.**  
> Baseado na arquitetura **System One** (estilo JEV da TypeSafe AI), executado 100% offline via **Ollama 0.35+**.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.9+](https://img.shields.io/badge/Python-3.9+-brightgreen.svg)](pyproject.toml)
[![Protocol: MCP](https://img.shields.io/badge/Protocol-MCP%202024--11--05-orange.svg)](https://modelcontextprotocol.io)
[![Backend: Ollama](https://img.shields.io/badge/Ollama-0.35+-black.svg)](https://ollama.com)

---

## ⚡ O que é o SystemOne Gate?

Enquanto agentes generativos como Claude, Gemini e GPT-4 geram textos extensos token a token (*System Two thinking*), o **SystemOne Gate** atua como o sistema reflexo (*System One thinking*) do ecossistema de inteligência artificial.

Ele avalia dados estruturados em paralelo gerando apenas **1 a 3 tokens de saída** com probabilidades do modelo (não calibradas; veja o campo `confidence`) para decisões críticas:

* 🩺 **Triagem de Erros:** Classifica se uma falha é de compilação, sintaxe, linkedição, memory leak ou timeout.
* 🔍 **Code Review de Diffs:** Mede a probabilidade de breaking change e risco arquitetural antes de cada commit.
* 🛡️ **Guardrail de Comandos Shell:** Avalia se um comando de terminal pode apagar dados ou quebrar o ambiente, com baixa latência local (veja [Desempenho medido](#-desempenho-medido)).
* 🔀 **Roteamento de Subagentes:** Decide para qual subagente encaminhar uma tarefa de desenvolvimento.

---

## 📊 Modelos Suportados (via Ollama)

| Modelo | Tamanho | Provedor | Latência | Caso de Uso Ideal |
| :--- | :--- | :--- | :--- | :--- |
| **`nimble`** | 9.5 GB (9B) | Bespoke Labs | ~390-410 ms (medido, veja abaixo) | Padrão da revisão de diff (pre-commit), code review profundo, detecção de breaking changes e triagem de erros complexos. |
| **`tev1:0.8b`** | 811 MB (0.8B) | Together AI | ~145-165 ms (medido, veja abaixo) | Guardrail de comandos shell com baixa latência local. **Não discrimina o risco de um diff** (veja o parágrafo do pre-commit hook). |
| **`tev1:4b`** | ~2.5 GB (4B) | Together AI | não medido | Equilíbrio intermediário entre velocidade e precisão. |


---

## 🧪 Desempenho medido

Medido em 2026-10-05 com `SystemOneClient` (chamadas sequenciais, Ollama local, GPU RTX 3060 12 GB, 100% GPU, CPU de 28 threads, máquina ociosa). Procedimento: 2 chamadas de aquecimento descartadas + 30 chamadas cronometradas (latência de ponta a ponta, incluindo HTTP e JSON). Reproduza com `python benchmarks/latency.py --cold`.

| Modelo | Payload | p50 | p95 | min / max | Partida a frio* |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `tev1:0.8b` | rubrica guard, comando curto | 162,6 ms | 170,1 ms | 150,2 / 170,8 ms | ~3-4 s |
| `tev1:0.8b` | rubrica diff-risk, diff de ~100 linhas | 143,2 ms | 160,5 ms | 133,3 / 166,2 ms | - |
| `nimble:latest` | rubrica guard, comando curto | 391,6 ms | 401,2 ms | 366,3 / 409,0 ms | ~12-72 s |
| `nimble:latest` | rubrica diff-risk, diff de ~100 linhas | 405,1 ms | 423,7 ms | 377,1 / 428,7 ms | - |

\* Primeira chamada após `ollama stop <modelo>` (modelo descarregado da memória). A faixa do `nimble` vem de duas medições independentes que divergiram (≈11,8 s com a máquina ociosa, ≈46,5 s numa execução com carga concorrente no Ollama e ≈72 s na primeira chamada do benchmark de rubricas); o valor real varia com o cache de disco do sistema e com a carga da máquina. Em parte desses casos o `SYSTEMONE_TIMEOUT` padrão de 30 s não basta para a primeira chamada do `nimble`.

* Estes números foram medidos com as rubricas ainda em português. A troca para inglês (2026-10-06) muda o tamanho do prompt e **não foi remedida**; rode `python benchmarks/latency.py` para atualizar.
* A latência depende de hardware, de o modelo já estar residente na memória e do tamanho do payload; não extrapole estes números para outra máquina. O diff de teste usa linhas curtas porque o endpoint rejeita entradas acima de ~2050 tokens.
* A camada de **regras determinísticas** (`guard_rules.evaluate_command`, offline, sem modelo) é o caminho rápido: ~40-50 µs por chamada (1000 chamadas, mesma máquina). O veredito do modelo é uma heurística adicional, não a barreira de segurança.
* Reprodutibilidade: 20 chamadas idênticas ao `tev1:0.8b` e 20 ao `nimble:latest` (rubrica guard) devolveram respostas idênticas, inclusive as probabilidades. Isso foi observado nesta máquina e versão do Ollama; não é uma garantia documentada pelo fabricante.

---

### Manter o modelo carregado (`OLLAMA_KEEP_ALIVE`)

O Ollama descarrega um modelo depois de um tempo sem uso; **o padrão é 5 minutos**. A primeira chamada depois disso paga o carregamento (medido: ≈12 a ≈72 s para o `nimble`, ≈3-4 s para o `tev1:0.8b`), e é por isso que o pre-commit hook, que usa o `nimble`, tem timeout de 120 s. Manter o modelo carregado evita esse custo.

**Como ver o que está carregado:** `ollama ps` mostra o modelo, o tamanho, o processador e, na coluna `UNTIL`, quando ele será descarregado.

**Como configurar:** é uma configuração do **servidor** Ollama (vale para todos os clientes). A variável `OLLAMA_KEEP_ALIVE` aceita:

| Valor | Efeito |
| :--- | :--- |
| `30m`, `24h` | mantém carregado por esse tempo após o último uso |
| `3600` | número de segundos |
| `-1` | mantém carregado **indefinidamente** |
| `0` | descarrega logo após a resposta |

No Linux, com o serviço systemd instalado pelo instalador oficial:

```bash
sudo systemctl edit ollama.service
# no editor, acrescente:
#   [Service]
#   Environment="OLLAMA_KEEP_ALIVE=30m"
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

Sem systemd, defina a variável ao iniciar o servidor: `OLLAMA_KEEP_ALIVE=30m ollama serve`. Para macOS e Windows, siga o [FAQ oficial do Ollama](https://docs.ollama.com/faq). Reiniciar o serviço descarrega o que estava na memória.

**Custo:** o modelo fica ocupando memória enquanto estiver carregado. Na máquina de teste (RTX 3060 de 12 GB) o `nimble` ocupou 8,9 GB, segundo o `ollama ps`. Prefira um valor finito que cubra uma sessão de trabalho (por exemplo `30m` a `1h`); use `-1` só em uma máquina dedicada, porque nesse caso a memória só é liberada ao reiniciar o serviço ou com `ollama stop <modelo>`.

**O que o SystemOne Gate não faz:** ele **não** define `keep_alive` por requisição. A documentação do Ollama lista esse parâmetro para `/api/generate` e `/api/chat`, não para o `/v1/systemone`, e não foi verificado que este endpoint o aceite.

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
# 1. Tev1 (0.8B) - Leve e rápido (811 MB de download)
# Ideal para qualquer máquina, baixa latência (veja Desempenho medido). Útil para checagem de comandos shell.
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

# Qualquer modelo Ollama (ordem: --model/--nimble > SYSTEMONE_DIFF_MODEL > nimble)
systemone-gate diff --model NOME

# Triagem de erro de build ou teste
systemone-gate triage "undefined reference to mqtt3_db_open no mosquitto.c"

# Testar se um comando de terminal é seguro
systemone-gate guard "rm -rf /tmp/data/*"

# Mostrar a versão instalada
systemone-gate --version

# Instalar Git Pre-Commit Hook no repositório atual
systemone-gate install-hook

# Remover o hook (restaura o hook original, se houver backup)
systemone-gate uninstall-hook

# Diagnosticar o backend Ollama (versão, modelos e contrato do endpoint)
systemone-gate doctor
```

**Diagnóstico (`doctor`):** o SystemOne Gate depende de um endpoint de terceiros sem contrato versionado, então este é o caminho rápido para investigar erros como "Failed to connect" ou HTTP 404. O comando verifica, em ordem: (1) se o Ollama responde em `/api/version`; (2) se a versão é **>= 0.35.0** (mínimo exigido; antes disso `/v1/systemone` não existe); (3) se os modelos `tev1:0.8b` e `nimble` estão instalados e com a capability `decision` (use `--model NOME`, repetível, para trocar a lista); (4) um teste de contrato com uma chamada mínima a `/v1/systemone` (pule com `--no-smoke`). Imprime um checklist (✅/⚠️/❌) e sai com 0 se tudo obrigatório passou, 1 se houve falha e 2 para configuração inválida (ex.: `SYSTEMONE_TIMEOUT`).

**Modelo do pre-commit hook:** o hook revisa o diff com o `nimble` por padrão. No benchmark de rubricas ([`docs/BENCHMARK_RUBRIC_LANGUAGE.md`](docs/BENCHMARK_RUBRIC_LANGUAGE.md); 36 diffs rotulados por um LLM, uma máquina; isso não é calibração) o `tev1:0.8b` **não discriminou o risco do diff** (acertou 36-39% do nível de risco, contra 33% do acaso, e 33-53% de `breaking_change`, contra 56% de quem responde sempre `safe`), enquanto o `nimble` acertou 72-75% do risco e 69% do `breaking_change`. **Custo:** o Ollama descarrega o modelo depois de um tempo parado (padrão do Ollama: 5 minutos; veja [Manter o modelo carregado](#manter-o-modelo-carregado-ollama_keep_alive)), e a primeira chamada depois disso leva de ≈12 a ≈72 s para carregar o `nimble`. Por isso o hook usa **timeout de 120 s** por padrão (a CLI e a biblioteca seguem com 30 s); defina `SYSTEMONE_TIMEOUT` para mudar. Para voltar ao modelo rápido: `SYSTEMONE_DIFF_MODEL=tev1:0.8b git commit ...`. **Hooks já instalados** só ganham o timeout de 120 s se forem reinstalados (`systemone-gate install-hook`); sem isso, o primeiro commit depois de uma pausa pode estourar 30 s e a revisão é pulada com um aviso.

**Ignorar o hook:** `SYSTEMONE_SKIP=1 git commit ...` pula apenas a verificação do SystemOne Gate (os demais hooks continuam valendo). Evite `git commit --no-verify`, que desativa todos os hooks.

**Saída sem emoji:** se o terminal ou o pipe não suporta UTF-8 (ex.: `PYTHONIOENCODING=ascii`), a CLI troca os emoji por tokens ASCII (`[OK]`, `[ERROR]`, `[WARN]`) automaticamente. Para forçar esse modo, use `systemone-gate --plain diff` ou `SYSTEMONE_PLAIN=1`. O servidor MCP não é afetado.

**Timeout:** o padrão é 30 s por chamada. No primeiro uso após inicialização a Ollama carrega o modelo em memória (o Nimble tem 9,5 GB) e pode demorar mais; aumente com `SYSTEMONE_TIMEOUT` (segundos, número positivo), por exemplo `SYSTEMONE_TIMEOUT=120 systemone-gate triage "..."`. Um valor inválido encerra a CLI com código 2. O endpoint pode ser trocado com `OLLAMA_SYSTEMONE_URL`.

**Confiança mínima (opt-in):** `SYSTEMONE_MIN_CONFIDENCE` (0 a 1) trata como indeterminado, e aplica a política `*_ON_ERROR`, o veredito cuja `confidence` seja menor que o mínimo; vem desligada (`0`) e sem valor sugerido porque não há calibração e as confianças observadas são baixas em todas as respostas (0,03 a 0,27), então qualquer mínimo alto bloquearia ou avisaria sempre.

---

### Perfis de rubrica (`diff` e `triage`)

As rubricas de risco de diff e de triagem de erro têm perfis, pois o texto original é voltado a código C/sistemas (sockets, locks, parsing de protocolo). O texto de todas as rubricas enviado ao modelo é em **inglês** (projeto público e internacional; ver [`docs/BENCHMARK_RUBRIC_LANGUAGE.md`](docs/BENCHMARK_RUBRIC_LANGUAGE.md)); as chaves de escolha, que a política lê, não mudam:

| Perfil | Para quê |
|---|---|
| `default` | Texto original (C/sistemas), mantido por compatibilidade. |
| `generic` | Redação neutra, sem jargão de C/rede. |
| `web-backend` | Serviços NestJS/Prisma/PostgreSQL/Java Spring: migrations destrutivas, autenticação/autorização, queries sem filtro de tenant, contratos REST/GraphQL, transações, segredos. |

Seleção: `systemone-gate diff --profile web-backend`, `systemone-gate triage --profile web-backend "erro"` ou a variável `SYSTEMONE_PROFILE` (o argumento tem precedência). Perfil inválido encerra com código 2. No MCP e na biblioteca vale a variável de ambiente, ou `profile=` em `review_diff`/`triage_error`. As rubricas `guard` e de roteamento não mudam.

> **Atenção:** os perfis `generic` e `web-backend` ainda **não foram validados** contra dados rotulados; a qualidade da nova redação não foi medida. O `default` continua sendo o texto original, voltado a C/sistemas.

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
3. `systemone_command_guard`: Verificação de segurança de comandos bash (modelo leve Tev1 0.8B; veja Desempenho medido).
4. `systemone_query`: Consultas arbitrárias tipadas (`choice` ou `score`) para qualquer contexto.
5. `systemone_review_staged`: Revisa o que está staged (`git diff --cached` lido pelo próprio servidor, por arquivo, ignorando lockfiles/binários) e devolve risco, cobertura e a decisão allow/block. Preferível ao `systemone_review_diff` quando a mudança já está staged: o diff não passa pelo agente (menos tokens de saída e sem risco de resumo/alteração).

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
* 📘 [**Manual Completo para Agentes de IA**](docs/AGENT_MANUAL.pt-BR.md) (Claude, Cursor, Windsurf, Cline, Aider, Antigravity, LangChain)
* 💡 [Exemplo de Script em Python para Agentes](examples/python_agent_integration.py)
* 📋 [Configuração para Cursor IDE](examples/cursor_rules.pt-BR.md)

---

## 🔒 Privacidade e Segurança

* **100% Local:** Todo o processamento acontece dentro da máquina do desenvolvedor (`localhost:11434`). O endpoint deve usar `http`/`https` e apontar para um host de loopback (`localhost`, `127.0.0.0/8`, `::1`, `*.localhost`); hosts remotos só são aceitos com `SYSTEMONE_ALLOW_REMOTE=1` (um aviso é emitido no stderr, pois diffs, comandos e logs passarão a sair da máquina). Nenhum nome de host é resolvido via DNS: qualquer um diferente de `localhost`/`*.localhost` conta como remoto.
* **Redação de segredos (ativa por padrão):** antes de enviar ao modelo, o texto (`state`: diffs, comandos, logs) passa por `redact_secrets`, que troca chaves AWS, tokens GitHub/Slack/Stripe, chaves Google, blocos PEM de chave privada, JWTs, `Authorization: Bearer ...`, senhas em URLs e atribuições `password|secret|api_key|token=...` por `[REDACTED:<regra>]`. Quando algo é mascarado, o resultado traz `"redacted": <n>`. Desative com `SYSTEMONE_REDACT=0` ou `SystemOneClient(redact=False)`. É uma redução de risco baseada em padrões, **não uma garantia**: formatos não reconhecidos passam. As regras determinísticas do `guard_command` enxergam o comando original, sem redação.
* **Sem Telemetria:** O SystemOne Gate não coleta e não envia dados para a nuvem.
* **Resiliente a Falhas de Rede:** Se o serviço local do Ollama estiver inativo, o pre-commit hook permite o fluxo normal de desenvolvimento para nunca bloquear o usuário.

---

## 🛠️ Desenvolvimento

```bash
pip install -e ".[dev]"   # pytest, ruff e mypy
scripts/check.sh          # ruff check → ruff format --check → mypy → pytest
```

- **ruff** (`E,F,W,I,B`): estilo, imports não usados/ordenados e armadilhas comuns (bugbear); também formata. O `format --check` só cobre arquivos Python **novos** (os existentes não foram reformatados em massa); `FORMAT_ALL=1 scripts/check.sh` verifica tudo.
- **mypy**: checagem de tipos de `systemone_gate/` (os testes não são tipados).
- **pytest**: suíte hermética (não chama o Ollama real; o teste `contract` é opt-in).
- Não há CI de propósito: as verificações são locais.

---

## 📄 Licença

Distribuído sob a licença [MIT](LICENSE).
