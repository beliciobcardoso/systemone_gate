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
4. **Autorização 3** ("pode publicar a versão") → tag `vX.Y.Z` em `main` e GitHub Release (ver "Releases").

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

## Releases

Versionamento semântico ([SemVer 2.0](https://semver.org/lang/pt-BR/)). Enquanto o projeto é `0.x`:

- mudança **incompatível** (comandos e flags da CLI, ferramentas MCP, variáveis de ambiente ou API da biblioteca) sobe o **minor** (`0.2.0` → `0.3.0`);
- correção ou melhoria **compatível** sobe o **patch**;
- o `1.0.0` só chega quando a API pública estiver declarada estável (comandos, flags e códigos de saída da CLI; nomes e schemas das ferramentas MCP; funções públicas de `systemone_gate`). A decisão é do mantenedor.

**Fonte única:** `__version__` em `systemone_gate/__init__.py`. O `pyproject.toml` (versão dinâmica), a CLI (`--version`) e o servidor MCP leem dele, e `tests/test_version.py` falha se algo divergir ou se o CHANGELOG não tiver a seção da versão.

Passos de um release:

1. Criar `chore/release_X_Y_Z` a partir de `dev` atualizada.
2. Editar **somente** `__version__`; no `CHANGELOG.md`, mover o conteúdo de `[Unreleased]` para `## [X.Y.Z] - AAAA-MM-DD`, deixando `[Unreleased]` vazio no topo. **Confira cada item contra `git log --first-parent vANTERIOR..dev`:** só entra na nova seção o que a tag anterior ainda não tem (um item mergeado antes da promoção pertence à versão anterior).
3. Rodar `scripts/check.sh` e abrir o PR para `dev` (squash).
4. Promover `dev` → `main` por merge commit (Autorização 2).
5. Criar a tag anotada no commit de merge em `main`: `git tag -a vX.Y.Z -m "release X.Y.Z" <sha>` e `git push origin vX.Y.Z`. Opcional: `gh release create vX.Y.Z --notes-file <trecho do CHANGELOG>`.

Tag e GitHub Release são ações públicas e exigem **autorização própria** (Autorização 3), separada da promoção. Nunca mover nem apagar uma tag já publicada: se um release sair errado, publique um patch novo.

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

## Convenções de trabalho (agentes)

- **Relatório de problemas:** ao concluir um item de `docs/ANALISE_PROBLEMAS.md`, marcar o status (✅ Resolvido / 🟡 Parcial / ⬜ Aberto) na **mesma branch** da correção, citando o PR; o PR vai para `dev`. Usar "Parcial" quando só parte foi resolvida.
- **Scripts de verificação que criam repositórios temporários:** usar `set -u`, criar o diretório com `mktemp -d` e abortar se falhar, conferir `$PWD` após o `cd`, exportar `GIT_CEILING_DIRECTORIES` para o diretório temporário e preferir `git -C <dir>` a `cd`. Nunca rodar `git init`/`git add`/`rm` com `cd` em variável possivelmente vazia (vai para o `$HOME`).
- **`python -m pacote`** põe o cwd em `sys.path` antes do `PYTHONPATH`: para testar outra worktree, rodar de um cwd neutro.

## Antes de abrir PR

- [ ] Testes passam e cobertura mínima de 80% nos módulos alterados.
- [ ] Nenhum segredo, `console.log`/`print` de debug ou código morto.
- [ ] Entradas externas validadas; erros tratados sem vazar detalhe interno.
- [ ] Documentação (README/docs) atualizada se o comportamento mudou.
