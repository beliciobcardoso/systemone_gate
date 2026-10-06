# Benchmark: rubricas em português vs inglês (RSK-02 / S3)

> **Atualização (2026-10-06): decisão do mantenedor, as rubricas de produção passam a ser em inglês.**
> O projeto é público, open source e de uso internacional; rubricas em português seriam uma barreira para
> contribuidores e para quem lê o que é enviado ao modelo. A decisão **não** se apoia em ganho de acurácia:
> pelo benchmark abaixo não há diferença detectável entre os idiomas (menor p = 0,19), de modo que a troca não
> degrada o que foi medido. A "Regra de decisão" abaixo continua sendo o critério para trocar *por
> desempenho*; aqui a troca é por público-alvo. Consequências:
> - todas as rubricas (`default`, `generic`, `web-backend`, `RUBRIC_COMMAND_SAFETY`, `RUBRIC_AGENT_ROUTING`)
>   estão em inglês em `systemone_gate/rubrics.py`; as chaves de escolha (os rótulos que a política lê) não mudaram;
> - o texto do perfil `default` é a tradução fiel que foi medida neste benchmark;
> - o original em português ficou congelado em `benchmarks/data/rubrics_pt.py`, para o benchmark continuar
>   reproduzível: a condição `pt` é esse arquivo e a condição `en` é a rubrica de produção;
> - os perfis `generic` e `web-backend`, `RUBRIC_COMMAND_SAFETY` e `RUBRIC_AGENT_ROUTING` **não foram medidos** neste
>   benchmark; o guard foi comparado em uma verificação posterior (seção "Verificação do guard" no fim);
> - os scores mudam com o prompt: qualquer coleta de calibração feita com a rubrica em português é inválida
>   (o hash da rubrica registrado na coleta detecta isso).
>
> O restante deste documento é o registro do experimento original e foi mantido como estava, inclusive
> "Decisão: manter português" abaixo, que era a conclusão *por desempenho* na data.

Experimento. Nenhum código de produção nem default foi alterado.

## Pergunta

As rubricas enviadas a cada requisição (`instructions` e `criteria` dos scores) estão em português. Os modelos
de decisão (`tev1:0.8b`, `nimble:latest`) talvez tenham sido treinados majoritariamente em inglês. A rubrica em
português degrada a acurácia em relação a uma tradução fiel em inglês?

## Método

- Condições: idioma {pt, en} x modelo {`tev1:0.8b`, `nimble:latest`} x tarefa {triage, diff} = 8 condições, 284 requisições.
- `pt` = rubricas do perfil `default`, então em produção (`RUBRIC_ERROR_TRIAGE`, `RUBRIC_DIFF_RISK`); hoje congeladas em
  `benchmarks/data/rubrics_pt.py`.
- `en` = tradução fiel (então em `benchmarks/data/rubrics_en.py`, hoje a própria rubrica de produção): mesmas chaves de pergunta, mesmas chaves de escolha
  (são os rótulos, não traduzidas), mesmo número e ordem de critérios de score. Só `instructions` e descrições de
  `criteria` foram traduzidas, sem acrescentar nem remover informação. Um teste hermético
  (`tests/test_benchmark_stats.py`) verifica essa paridade estrutural.
- Mesmos casos nas duas línguas (pareamento). Uma passada por condição, requisições estritamente sequenciais,
  `SystemOneClient(redact=False)`, timeout de 120 s. Nenhuma requisição falhou nem foi rejeitada (HTTP 400): o maior
  `input_tokens` observado foi 908, bem abaixo do limite de ~2050.
- Predição: argmax de `probabilities` (para o score de risco, chaves "0","1","2"). O `choice` retornado pelo modelo
  concordou com o argmax em 100% das respostas de escolha (triage e `breaking_change`, as 8 condições).
- Métricas: acurácia, IC 95% de Wilson, macro-F1, confiança média, MAE do `score` esperado vs rótulo (risco),
  matrizes de confusão, e comparação pareada pt vs en com McNemar exato bicaudal (binomial sobre os pares
  discordantes, implementado com `math.comb` em `benchmarks/rubric_language_stats.py`, com testes unitários).
- Reprodução: `python benchmarks/rubric_language.py [--models ...] [--tasks ...] [--limit N] [--dry-run]`;
  predições brutas por caso, data, versões e hardware em `benchmarks/results/rubric_language.json`.

## Dataset e limites

Os dois arquivos (`triage_cases.json`, `diff_cases.json`) e seus rótulos foram escritos **antes** de qualquer
execução dos modelos e nenhum rótulo foi alterado depois de ver saídas (um smoke test de 3 casos por tarefa foi
rodado só para validar o runner; seu resultado foi descartado).

- `triage_cases.json`: 35 trechos de erro/log (1 a 6 linhas; gcc/clang, ld/lld, valgrind/ASan/LSan, Python,
  Node, Java, Go, pytest/Jest/JUnit, MQTT, gRPC, Modbus, npm/pip), 5 por classe das 7 chaves do perfil default.
- `diff_cases.json`: 36 diffs pequenos (<= 25 linhas), 12 por nível de risco (0/1/2), com `breaking_change`
  em {safe, potential_break, breaking_change}. Linguagens: C 14, Python 9, SQL 5, TypeScript 5, shell 3.
  Distribuição de `breaking_change`: safe 20, potential_break 8, breaking_change 8 (desbalanceada por construção:
  diffs de risco 0 são sempre `safe`). Por isso o baseline "sempre safe" acerta 55,6% e o macro-F1 é a
  métrica mais honesta nessa pergunta.
- Cada caso tem `why` com a justificativa do rótulo.

Limites, a serem lidos antes de qualquer conclusão:

- Rótulos escritos por um LLM, não por humanos independentes. Casos "óbvios" por construção tendem a inflar a
  acurácia do triage (teto próximo de 100%).
- n pequeno (35 e 36): os IC de Wilson têm ~25 pontos percentuais de largura.
- Uma máquina (RTX 3060 12 GB, Ollama 0.35.1), dois modelos, uma passada (as saídas são determinísticas).
- Rótulos de diff subjetivos: o nível de risco segue literalmente os critérios do perfil default (alto =
  concorrência, locks, alocação de memória, structs de socket). Mudanças de contrato público sem esses
  elementos (ex.: renomear função exportada, renomear coluna SQL) foram rotuladas nível 1 e `breaking_change`; um
  revisor humano poderia rotulá-las como nível 2. Isto é um problema conhecido de rótulo, não corrigido.
- Rótulos de triage mais discutíveis: tri-33 (`make: protoc: Command not found`, `environment_or_missing_dep`; ambos
  os modelos às vezes dizem `linker`) e tri-02/tri-05 (erros de compilador TS/clang). Mantidos como escritos.
- Testes múltiplos (6 McNemar): nenhum ajuste foi aplicado; como nenhum teste é significativo, isso não muda a conclusão.

## Resultados por condição

Acurácia [IC 95% Wilson], macro-F1, confiança média (n = 35 triage, 36 diff).

| Tarefa / métrica | Modelo | pt | en |
|---|---|---|---|
| triage `root_cause` | tev1:0.8b | 0,886 [0,740-0,955]; F1 0,892; conf 0,811 | 0,943 [0,814-0,984]; F1 0,944; conf 0,840 |
| triage `root_cause` | nimble | 0,943 [0,814-0,984]; F1 0,940; conf 0,897 | 0,971 [0,855-0,995]; F1 0,971; conf 0,916 |
| diff `risk_level` | tev1:0.8b | 0,389 [0,248-0,551]; F1 0,292; MAE 0,742; conf 0,314 | 0,361 [0,225-0,524]; F1 0,268; MAE 0,820; conf 0,331 |
| diff `risk_level` | nimble | 0,750 [0,589-0,862]; F1 0,753; MAE 0,326; conf 0,587 | 0,722 [0,560-0,842]; F1 0,710; MAE 0,304; conf 0,605 |
| diff `breaking_change` | tev1:0.8b | 0,333 [0,202-0,497]; F1 0,249; conf 0,267 | 0,528 [0,370-0,680]; F1 0,230; conf 0,292 |
| diff `breaking_change` | nimble | 0,694 [0,531-0,820]; F1 0,523; conf 0,576 | 0,694 [0,531-0,820]; F1 0,550; conf 0,576 |

Matrizes de confusão completas em `benchmarks/results/rubric_language.json` (`summary.conditions`). Pontos relevantes:

- `tev1:0.8b` em diff está praticamente colapsado em uma classe. Risco: em pt prevê quase tudo como nível 1
  (acerta 14/36 contra 12/36 de acaso por classe), em en prevê quase tudo como nível 0 (13/36). Nenhum
  dos dois acerta o nível 2 (0/12).
- `tev1:0.8b` `breaking_change` em en: todos os 19 acertos vêm de prever `safe` em 35 de 36 casos (19 + 0 + 0 corretos)
  (matriz [[19,1,0],[8,0,0],[8,0,0]]), exatamente o baseline majoritário (55,6%). Os 52,8% **não** indicam melhora de
  discernimento, por isso o macro-F1 cai (0,249 para 0,230). Em pt, o modelo espalha as predições
  (potential_break 28 vezes) e erra mais.
- `nimble` em diff `risk_level` acerta nível 2 quase sempre (10/12 pt, 11/12 en) e erra sobretudo nível 1 como 0.
  `nimble` nunca acerta `potential_break` em nenhum idioma (0/8).
- Erros de triage: pt tev1 (4): tri-03, tri-05, tri-20, tri-33; en tev1 (2): tri-05, tri-33; pt nimble (2): tri-02, tri-05;
  en nimble (1): tri-02. Erros concentrados nos mesmos casos nas duas línguas.

## Comparação pareada pt vs en (mesmos casos)

| Tarefa | Modelo | ambos certos | só pt | só en | ambos errados | p (McNemar exato) |
|---|---|---|---|---|---|---|
| triage `root_cause` | tev1:0.8b | 31 | 0 | 2 | 2 | 0,500 |
| triage `root_cause` | nimble | 33 | 0 | 1 | 1 | 1,000 |
| diff `risk_level` | tev1:0.8b | 6 | 8 | 7 | 15 | 1,000 |
| diff `risk_level` | nimble | 25 | 2 | 1 | 8 | 1,000 |
| diff `breaking_change` | tev1:0.8b | 5 | 7 | 14 | 10 | 0,189 |
| diff `breaking_change` | nimble | 24 | 1 | 1 | 10 | 1,000 |

## Determinismo

5 casos aleatórios por modelo (seed fixa; 10 reenvios da rubrica pt, mistura de triage e diff): todas as
`probabilities` idênticas bit a bit na segunda chamada (10/10). Portanto uma passada por condição basta e a variância
observada é só de amostragem de casos, não de execução.

## Interpretação

Por modelo e tarefa, no tamanho de amostra deste experimento:

| Tarefa | tev1:0.8b | nimble:latest |
|---|---|---|
| triage | nenhuma diferença detectável (en +5,7 pp, 2 discordâncias, nenhuma a favor de pt) | nenhuma diferença detectável (+2,9 pp, 1 discordância) |
| diff `risk_level` | nenhuma diferença detectável (modelo sem poder discriminativo nos dois idiomas) | nenhuma diferença detectável (-2,8 pp, 3 discordâncias) |
| diff `breaking_change` | nenhuma diferença detectável (en +19 pp na acurácia, p = 0,19, e o ganho é artefato de prever `safe`) | nenhuma diferença detectável (idêntico, 2 discordâncias) |

Leitura honesta: a direção do ponto estimado favorece levemente o inglês no triage (nos dois modelos, sempre em
zero ou poucos casos discordantes), mas com 1 a 2 discordâncias isso é indistinguível de ruído. Em diff não há
direção consistente (risco: pt melhor na acurácia nos dois modelos, en melhor no MAE do nimble; diferenças de 1 a 3 casos). A diferença maior (tev1, `breaking_change`) é
explicada por colapso para a classe majoritária. O fator dominante nos resultados é o **modelo e a tarefa** (nimble >> tev1 em
diff; triage ~90%+ nos dois), não o idioma da rubrica.

Ausência de evidência não é evidência de ausência: com n ~ 35 e 0 a 15 pares discordantes, o teste só detectaria
efeitos grandes (por exemplo 7 a 0 discordantes para p < 0,05). Um efeito de poucos pontos percentuais, se existir,
não foi medido.

### Regra de decisão

(Esta regra e a decisão abaixo eram o critério *por desempenho*; foram superadas pela decisão do mantenedor no topo
deste documento.)

Mudar o idioma default de produção SOMENTE se en superar pt com p < 0,05 para AMBOS os modelos na mesma tarefa.
Resultado: nenhuma tarefa atinge p < 0,05 em nenhum modelo (menor p = 0,189). **Decisão: manter português.** Nenhum
código nem default foi alterado.

Se a questão voltar a importar, o follow-up seria um dataset maior (centenas de casos, rótulos de revisores humanos
independentes, diffs reais do repositório) e, para o `tev1:0.8b` em diff, tratar antes o problema maior: o modelo não
discrimina risco em nenhum idioma.

## Uso futuro

Este conjunto rotulado pode servir de semente para calibração de limiares (FAL-03/FAL-05), mas **não é suficiente**
para isso: poucos casos, rótulos de LLM, distribuição sintética e `breaking_change` desbalanceado.

## Verificação do guard após a troca para inglês (2026-10-06)

O benchmark acima cobre só triagem e diff do perfil `default`. `RUBRIC_COMMAND_SAFETY` (guard) foi comparada depois,
com os 84 comandos sintéticos do dataset de calibração (`benchmarks/data/calibration/`), coletados nos dois idiomas
com `benchmarks/calibrate_collect.py` e analisados com `benchmarks/calibrate_analyze.py --preliminary`.

Reprodução: as duas coletas brutas estão em `benchmarks/results/guard_rubric_language_pt.json` e
`guard_rubric_language_en.json` (mesmo digest do `tev1:0.8b`, `8d11b3146b7f…`; a coleta `en` foi feita com o pacote ainda
sem commit, `git_dirty_package: true`, antes desta mudança). O texto da rubrica `pt` do guard está congelado em
`benchmarks/data/rubrics_pt.py` (`RUBRIC_COMMAND_SAFETY_PT`). A diferença de AUC sai de:

```bash
python benchmarks/compare_collections.py benchmarks/results/guard_rubric_language_pt.json \
    benchmarks/results/guard_rubric_language_en.json --labels pt en
```

Para coletar o lado `pt` de novo é preciso apontar a coleta para a rubrica congelada (não automatizado).

| Modelo | Idioma | AUC do `danger_score`* | Score médio bloqueáveis / seguros | Defaults atuais: recall / FPR |
|---|---|---|---|---|
| `tev1:0.8b` (modelo de produção do guard) | pt | 0,83 | 0,61 / 0,38 | 38,5% / 0,0% |
| `tev1:0.8b` | en | 0,72 | 0,44 / 0,33 | 38,5% / 0,0% |
| `nimble:latest` | pt | 0,92 | 1,67 / 0,75 | 87,2% / 11,1% |
| `nimble:latest` | en | 0,91 | 1,70 / 0,78 | 84,6% / 17,8% |

\* Bloqueáveis que as regras determinísticas não pegam (24) contra seguros (45). Diferença pareada de AUC (en − pt),
bootstrap pareado com 2000 reamostragens (semente fixa) sobre os 69 casos: `tev1:0.8b` **−0,104 [IC 95% −0,182; −0,030]**;
`nimble:latest` −0,014 [−0,040; +0,006].

Leitura:

- Para o `nimble` não há mudança detectável no ranqueamento, em linha com o benchmark original. Nos defaults atuais o
  FPR sobe de 11,1% para 17,8% (3 falsos positivos a mais em 45), dentro do que o n pequeno permite.
- Para o `tev1:0.8b`, a rubrica em inglês **piora** a separação entre comandos perigosos e seguros, e o intervalo
  exclui zero. Isto contraria "nenhuma diferença detectável" do benchmark de triagem/diff e vale para o guard.
- **Efeito prático hoje: nenhum.** Com o limiar padrão (1,5) o `tev1:0.8b` não bloqueia nada por conta própria em
  nenhum dos idiomas (recall igual ao das regras sozinhas, 38,5%); o guard efetivo continua sendo as regras
  determinísticas. O custo da troca está em calibrar o modelo como gate no futuro: o melhor recall fora da amostra da
  variante `score_only` foi 66,7% (pt) contra 64,1% (en), nenhum perto de 90%.
- Limites: rótulos sintéticos ainda sem revisão humana, n = 69, um único prompt em inglês. Uma redação diferente da
  rubrica em inglês poderia recuperar parte da diferença; não foi testada para não ajustar o prompt em cima de
  rótulos não revisados. Refazer esta comparação depois da revisão dos rótulos.
