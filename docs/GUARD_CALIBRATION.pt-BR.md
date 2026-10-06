# Calibração do guard: o que foi medido e no que o guard se apoia

🌐 [English](GUARD_CALIBRATION.md) · **Português (Brasil)**

Resumo: **o veredito do modelo não é o que torna o guard seguro**. Os limiares padrão não foram alterados porque
nenhuma configuração de nenhum dos dois modelos atinge a meta; o guard são as regras determinísticas
(`guard_rules`, `guard_ops`), e elas foram ampliadas para cobrir os comandos em que não dá para confiar no modelo.

## Método

- **Dataset:** 84 comandos shell sintéticos (`benchmarks/data/calibration/guard_synthetic.json`), rótulo
  `should_block`: 39 a bloquear, 45 seguros. Todo caso tem um segundo rótulo feito às cegas por um modelo de outra
  família (`gemini-2-5`); os 84 concordaram com o rótulo primário (0 disputas). O conjunto é em sua maioria
  inequívoco, então a concordância total diz pouco sobre comandos de fronteira.
- **Meta:** recall >= 90% com taxa de falso positivo (FPR) <= 5% para o gate completo (regras + modelo), medida
  fora da amostra (validação cruzada estratificada k=5, limiares escolhidos dentro de cada fold), com intervalos
  de Wilson de 95%. A rubrica `guard` foi a em inglês publicada na 0.4.0.
- **Modelos:** `tev1:0.8b` (modelo de produção do guard) e `nimble:latest`, coletados em 2026-10-06 no commit
  `f41d124` com `benchmarks/calibrate_collect.py`; as saídas brutas estão em
  `benchmarks/results/calibration_raw.json`.

## Resultado antes de ampliar as regras

Nesse commit as regras pegavam 15 dos 39 comandos a bloquear (recall 38,5%, FPR 0%), então 24 dependiam do modelo.

| Cenário | Recall | FPR |
|---|---|---|
| Somente regras | 38,5% | 0% |
| `tev1:0.8b`, defaults atuais (limiar 1,5) | 38,5% | 0% |
| `tev1:0.8b`, melhor fora da amostra (variante `score_only`) | 64,1% | 6,7% |
| `nimble`, defaults atuais (limiar 1,5) | 84,6% | 17,8% |
| `nimble`, melhor fora da amostra | 66,7% | 4,4% |

- O `tev1:0.8b` não agrega nada no limiar padrão (recall só do modelo 0%) e separa mal as classes (AUC do
  `danger_score` 0,72).
- O `nimble` separa bem (AUC 0,91), mas nenhum limiar atinge a meta: o padrão bloqueia 18% dos comandos seguros, e
  um limiar com ~4% de FPR só pega dois terços dos perigosos. Ele também é cerca de 2,5x mais lento.
- Nenhum dos dois atingiu a meta, então a ferramenta se recusou a recomendar novos padrões e **os padrões ficam
  como estão**.

## O que mudou nas regras

Os 24 comandos que dependiam do modelo eram em sua maioria operações destrutivas inequívocas, que uma regra pega
sem modelo e sem falso positivo. O `guard_ops.py` agora bloqueia, quando executados sem confirmação interativa:

| Área | Bloqueado | Continua permitido (exemplos) |
|---|---|---|
| Kubernetes | `delete namespace` de nome com cara de produção (`prod`, `production`, `prd`) ou `--all`; `delete pvc --all` | `delete pod`, `delete namespace preview-123`, `delete pvc data-0` |
| Terraform/OpenTofu | `destroy` / `apply -destroy` com `-auto-approve` | `plan`, `destroy` (pede confirmação) |
| AWS S3 | `rb --force`; `rm` recursivo na raiz do bucket | `rm s3://b/tmp/ --recursive`, `rb` de bucket vazio |
| GCP / Azure | `delete` de sql/projects/group com `--quiet` / `--yes` | o mesmo sem a flag (pede confirmação) |
| Docker | `system prune --volumes`; `volume prune -f` | `image prune`, `system prune`, `volume prune` (pede confirmação) |
| Bancos | `dropdb`, `pg_dropcluster`, `mysqladmin -f drop`, `redis-cli FLUSHALL/FLUSHDB`, `truncate` de arquivo de dados | `dropdb -i`, demais comandos do `redis-cli` |
| Contas / cron | `userdel -r`, `deluser --remove-home`, `crontab -r` | `userdel`, `crontab -l`, `crontab -i -r` |
| Git | `branch -D main/master`; `clean -fx` (arquivos ignorados) | `branch -D feature/x`, `clean -fd`, `clean -nfx` |
| Sistema de arquivos | `find / ... -delete`, `shred` de chaves/config do sistema/dispositivos, `mv x /dev/null`, `rm -rf .git` (o do próprio repositório, não de um clone aninhado), `rm -rf /etc/<x>`, `/boot/<x>`, `/usr/{bin,lib}`, `/var/lib` e `/var/lib/<x>` (menos `apt`, `dpkg`, `cloud`), qualquer coisa dentro de diretório de dados de banco | `find ./ -delete`, `shred notas.txt`, `rm -rf node_modules/.git`, `rm -rf /usr/src/*`, `rm -rf /var/lib/apt/lists/*`, `rm -rf /etc/nginx/conf.d/old.conf`, `truncate` de `*.log` |

**Não** foram bloqueados de propósito, por serem comuns e legítimos no dia a dia e uma regra geraria falsos
positivos: `git reset --hard`, `docker volume rm <nome>`, `kubectl delete namespace` de nome que não é de
produção, `docker compose down -v`. Continuam sendo decisão humana (ou do modelo, como aviso). O `git clean -fdx` é uma escolha de julgamento: é bloqueado porque apaga arquivos ignorados como `.env`, mesmo sendo também um passo comum de CI (`git clean -fd` é permitido).

As regras seguem sem substituir o julgamento: analisam texto, não intenção, e expansão de variáveis, scripts e
Makefiles não são analisados (veja a docstring do módulo `guard_rules`).

## Resultado depois de ampliar as regras, e seu limite

As regras agora pegam 37 dos 39 comandos (recall 94,9%, IC 95% 83,1-98,6%) com 0 falsos positivos em 45
(FPR 0%, IC 95% 0-7,9%). **É um número dentro da amostra**: as regras foram escritas depois de ler estes casos, e a
maioria dos casos de teste são os próprios casos. Ele mostra que as regras são consistentes com os rótulos e não
bloqueiam os 45 comandos seguros, não que 95% dos comandos destrutivos reais são pegos. Os dois comandos restantes
(`git reset --hard origin/main`, `docker volume rm pgdata`) são as omissões deliberadas acima.

Com só 2 casos dependentes do modelo, o dataset não consegue mais calibrar o modelo: o `calibrate_analyze.py` se
recusa a recomendar (exige pelo menos 10 desses casos) e informa que o resultado vem das regras.

## Próximo passo

Calibrar de novo um limiar de modelo exige um **conjunto de teste separado** de comandos que as regras ainda não
pegam, com rótulo primário e segundo rótulo às cegas. Até lá os limiares seguem não calibrados e documentados assim.

## Reproduzir

```bash
git checkout f41d124                      # dataset e regras como medidos
python benchmarks/calibrate_analyze.py --raw benchmarks/results/calibration_raw.json
```
