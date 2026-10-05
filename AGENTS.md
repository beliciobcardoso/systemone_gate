# AGENTS.md

Regras de fluxo Git para humanos e agentes neste repositório.

## Modelo de branches

```
feat/new_feature ──PR──► dev ──PR (promoção)──► main
```

| Branch | Papel | Push direto | Origem |
|---|---|---|---|
| `main` | Produção/estável. Só recebe promoções de `dev`. | Proibido | `dev` |
| `dev` | Integração. Recebe features. | Proibido | `feat/*`, `fix/*`, etc. |
| `<type>/<nome>` | Trabalho de uma feature/correção. | Livre | `dev` atualizada |

- Nome da branch de trabalho: `<type>/<nome_curto>` (ex.: `feat/new_feature`, `fix/cli_exit_code`, `docs/ollama_guide`). Usar `/`, nunca `\`.
- Sempre criar a branch a partir de `dev` atualizada (`git fetch && git switch -c feat/x origin/dev`).
- Um assunto por branch. Mudança não relacionada vai para outra branch.
- Nova feature = criar a branch `feat/<nome>` **antes** de editar qualquer arquivo.

## Gates de autorização (agentes)

O agente **não** avança de etapa sem autorização explícita do usuário em cada uma:

1. **Implementar** → trabalha na branch `feat/<nome>`, sem commitar.
2. **Autorização 1** ("pode commitar/subir") → commit, push e PR **`feat/<nome>` → `dev`**.
3. **Autorização 2** ("pode promover") → PR **`dev` → `main`**.

- Autorização vale só para a etapa citada e para aquele momento; não se estende a etapas seguintes nem a outras features.
- Merge dos PRs só quando o usuário pedir. Auto-merge só se solicitado.

## Commits

Formato (Conventional Commits), em inglês:

```
<type>: <description>

<optional body>

Co-Authored-By: Claude <noreply@anthropic.com>
```

- Tipos: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`.
- Descrição no imperativo, minúscula, sem ponto final, até ~72 caracteres.
- Corpo explica o **porquê**, não o quê. Separado do título por linha em branco.
- Commit feito em sessão com agente **deve** terminar com o trailer `Co-Authored-By`, separado do corpo por uma linha em branco. Usar a linha de atribuição indicada pela ferramenta/sessão em uso.
- Commits pequenos e atômicos: cada um compila e passa nos testes.
- Nunca commitar segredos (`.env`, chaves, tokens). Validar antes de `git add`.
- Proibido `--no-verify`, `--amend`/`rebase` em commits já publicados e `push --force` em `main`/`dev`.

## Pull Requests

### feature → `dev`

1. Push da branch com `-u`.
2. PR com base `dev`.
3. Analisar o histórico completo (`git diff dev...HEAD`), não só o último commit.
4. Título no formato de commit (`<type>: <description>`).
5. Descrição: **Resumo** (o que e por quê), **Test plan** (checklist) e riscos/breaking changes/migrations destrutivas, se houver.
6. Draft enquanto não estiver pronto para revisão.

### promoção `dev` → `main`

1. PR com base `main`, head `dev`. Título: `release: <resumo do que está sendo promovido>`.
2. Descrição lista os PRs/features incluídos desde a última promoção (`git log main..dev`), com breaking changes e passos de deploy/rollback.
3. Exige `dev` já validada.

Descrição de PR criado por agente termina com:

```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

## Merge

- **feature → `dev`**: **squash merge**. Um commit por feature em `dev`; os `Co-Authored-By` são agregados no commit final. Mensagem segue o formato de commit.
- **`dev` → `main`**: **merge commit** (não squash, não rebase). Squash na promoção faz `dev` divergir de `main` e gera conflitos fantasmas no ciclo seguinte.
- Só mergear com: branch atualizada com a base, conflitos resolvidos, conversas resolvidas.
- Após o merge de feature, apagar a branch remota e local. **Nunca** apagar `dev` nem `main`.
- Após promover, `main` pode ficar à frente de `dev` por um merge commit: sincronizar com `git switch dev && git merge --ff-only origin/main` (ou merge de `main` em `dev`).

## Proteção de branches

Configurar no GitHub (Settings → Rules → Rulesets, ou Branch protection):

### `main`
- Exigir PR; bloquear push direto.
- Restringir a origem dos PRs a `dev` por convenção (o projeto não usa CI para impor isso).
- Exigir ao menos 1 aprovação; descartar aprovações obsoletas em novos commits.
- Exigir branch atualizada com a base antes do merge.
- Exigir resolução de todas as conversas.
- Permitir apenas merge commit (histórico linear **não** se aplica aqui).
- Bloquear force push e deleção.
- Sem bypass, inclusive para administradores.

### `dev`
- Exigir PR; bloquear push direto.
- Permitir apenas squash merge.
- Bloquear force push e deleção.

## Antes de abrir PR

- [ ] Testes passam e cobertura mínima de 80% nos módulos alterados.
- [ ] Nenhum segredo, `console.log`/`print` de debug ou código morto.
- [ ] Entradas externas validadas; erros tratados sem vazar detalhe interno.
- [ ] Documentação (README/docs) atualizada se o comportamento mudou.
