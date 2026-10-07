# Teste de fumaça: perfil de diff `default` vs `web-backend`

> 🌐 [English](PROFILE_SMOKE_TEST.md) · **Português (Brasil)**

Verificação informal de como os perfis de risco de diff `default` e `web-backend` se comportam em alguns diffs feitos
à mão. **Isto não é calibração nem benchmark**: 8 casos sintéticos, um rótulo cada, uma execução cada, uma máquina.
Mostra direção, não acurácia. O README continua dizendo que os perfis `generic` e `web-backend` não foram validados
contra dados rotulados; este documento não muda isso.

## Preparação

- Data: 2026-10-07. SystemOne Gate 0.6.1, Ollama 0.35.1, modelo `nimble:latest`, RTX 3060 12 GB.
- Comando: `systemone-gate diff --nimble --profile <default|web-backend>`, em um repositório Git descartável com a
  mudança staged. Política padrão (bloqueia só se risco > 1,85 **e** `breaking_change` > 0,65).
- Uma chamada por caso e perfil, sem repetições. A verificação anterior de reprodutibilidade (respostas idênticas para
  chamadas idênticas, ver o README) é o que dá sentido a uma única execução, mas foi medida na rubrica do guard.
- Commit base: uma função Python de duas linhas, `total(items)`, em `a.py`.

## Resultados

| # | Caso (mudança staged) | `default` risco / breaking | `web-backend` risco / breaking | Decisão |
|---|---|---|---|---|
| 1 | docs: novo `README.md` | 0,02 / 1,0% | 0,02 / 1,1% | ambos aprovados |
| 2 | refactor cosmético (renomear variável) | 0,07 / 1,2% | 0,05 / 1,7% | ambos aprovados |
| 3 | assinatura quebrada (`tax` vira parâmetro obrigatório) | 1,01 / 44,4% | 1,52 / 66,0% | ambos aprovados |
| 4 | endpoint novo e isolado (`/health`) | 0,72 / 1,4% | 0,99 / 1,1% | ambos aprovados |
| 5 | migration destrutiva (`DROP COLUMN`, `DROP TABLE`) | 1,38 / 63,0% | **1,99 / 94,3%** | `default` aprovou, **`web-backend` bloqueou** |
| 6 | senha de banco hardcoded | 1,06 / 5,5% | 1,98 / 18,0% | ambos aprovados |
| 7 | senha em `.env` staged (vazou) | 0,62 / 5,2% | 1,97 / 17,0% | ambos aprovados |
| 8 | senha em `.env` ignorado (`.gitignore` staged, configuração correta) | 0,59 / 5,2% | 1,88 / 15,1% | ambos aprovados |

Uma primeira execução, com a mesma mudança em `a.py` mais uma senha hardcoded e uma chamada
`os.system("rm -rf " + HOME)`, deu `default` 1,70 / 66,5% (aprovado, com aviso de quase-bloqueio) e `web-backend`
1,98 / 80,5% (bloqueado).

## O que isso mostra

- **Sem falso positivo em mudanças inofensivas.** Os casos 1, 2 e 4 ficam baixos nos dois perfis.
- **O ganho mais claro é a migration destrutiva (caso 5).** O `default` descreve o risco em termos de C/sistemas
  (locks, sockets, alocação) e deixou passar; o `web-backend` a colocou no topo e bloqueou.
- **Segredos elevam o risco, mas não bloqueiam.** Os casos 6 a 8 recebem risco de 1,88 a 1,98 no `web-backend`, mas o
  `breaking_change` fica perto de 17%, e o bloqueio exige os dois valores acima dos limites. Segredo vazado não é
  quebra de contrato, então isso é coerente, mas não se deve contar com o hook para impedir commit de segredos.
- **O perfil não distingue um `.env` vazado de um ignorado.** Os casos 7 e 8 diferem em 0,09 de risco. O modelo só vê
  o texto do diff.
- **O caso 3 é limítrofe.** O `web-backend` passou do limite de `breaking_change` (66,0% > 65%), mas o risco (1,52)
  ficou longe de 1,85, então foi aprovado.

## Ressalva: o modelo não viu a senha

A redação de segredos vem ligada por padrão (`SYSTEMONE_REDACT`, ver o README). Nos casos 6 e 7 o texto enviado ao
modelo foi `postgres://admin:[REDACTED:url_password]@prod/db`, e não a senha real. Logo, o risco alto do `web-backend`
nesses casos é reação ao marcador `[REDACTED:...]` e a palavras como `DATABASE_URL` e `.env`, e não a avaliação de uma
senha que o modelo leu. É um comportamento razoável (o marcador avisa que há algo secreto), mas significa que esses
casos não testam se o perfil reconhece um segredo por conta própria. Uma execução com `SYSTEMONE_REDACT=0` testaria, e
não foi feita.

## O que não foi coberto

- Repetições ou qualquer estimativa de variância; rótulos de um segundo revisor.
- Diffs realistas (maiores, com vários arquivos, código de framework); todos os casos aqui têm de 1 a 6 linhas.
- `tev1:0.8b`, que o README já informa não discriminar o risco de diff.
- Detecção de segredos. Para isso use um scanner dedicado (por exemplo gitleaks ou detect-secrets); o SystemOne Gate
  não é um.

## Reproduzindo

```bash
D="$(mktemp -d)" || exit 1
export GIT_CEILING_DIRECTORIES="$D"
git -C "$D" init -q
printf 'def total(items):\n    return sum(i.price for i in items)\n' > "$D/a.py"
git -C "$D" add . && git -C "$D" commit -qm init
# faça o stage de uma mudança e compare os perfis:
printf 'ALTER TABLE users DROP COLUMN email;\nDROP TABLE audit_log;\n' > "$D/002_cleanup.sql"
git -C "$D" add 002_cleanup.sql
cd "$D" && for p in default web-backend; do systemone-gate diff --nimble --profile "$p"; done
```
