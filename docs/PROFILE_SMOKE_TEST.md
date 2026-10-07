# Smoke test: `default` vs `web-backend` diff profile

> 🌐 **English** · [Português (Brasil)](PROFILE_SMOKE_TEST.pt-BR.md)

An informal check of how the `default` and `web-backend` diff-risk profiles behave on a handful of hand-made diffs.
**This is not a calibration and not a benchmark**: 8 synthetic cases, one label each, one run each, one machine. It
shows direction, not accuracy. The README still says that the `generic` and `web-backend` profiles have not been
validated against labeled data; this document does not change that.

## Setup

- Date: 2026-10-07. SystemOne Gate 0.6.1, Ollama 0.35.1, model `nimble:latest`, RTX 3060 12 GB.
- Command: `systemone-gate diff --nimble --profile <default|web-backend>`, run in a throwaway Git repository with the
  change staged. Default policy (block only if risk > 1.85 **and** `breaking_change` > 0.65).
- One call per case and profile, no repetitions. The earlier reproducibility check (identical answers for identical
  calls, see the README) is what makes a single run meaningful, but it was measured on the guard rubric.
- Base commit: a two-line Python function `total(items)` in `a.py`.

## Results

| # | Case (staged change) | `default` risk / breaking | `web-backend` risk / breaking | Decision |
|---|---|---|---|---|
| 1 | docs: new `README.md` | 0.02 / 1.0% | 0.02 / 1.1% | both approved |
| 2 | cosmetic refactor (variable rename) | 0.07 / 1.2% | 0.05 / 1.7% | both approved |
| 3 | breaking signature (`tax` becomes a required parameter) | 1.01 / 44.4% | 1.52 / 66.0% | both approved |
| 4 | new isolated endpoint (`/health`) | 0.72 / 1.4% | 0.99 / 1.1% | both approved |
| 5 | destructive migration (`DROP COLUMN`, `DROP TABLE`) | 1.38 / 63.0% | **1.99 / 94.3%** | `default` approved, **`web-backend` blocked** |
| 6 | hardcoded database password | 1.06 / 5.5% | 1.98 / 18.0% | both approved |
| 7 | password in a staged `.env` (leaked) | 0.62 / 5.2% | 1.97 / 17.0% | both approved |
| 8 | password in an ignored `.env` (`.gitignore` staged, correct setup) | 0.59 / 5.2% | 1.88 / 15.1% | both approved |

A first run with the same `a.py` change plus a hardcoded password and an `os.system("rm -rf " + HOME)` call gave
`default` 1.70 / 66.5% (approved, with a near-miss warning) and `web-backend` 1.98 / 80.5% (blocked).

## What it shows

- **No false positive on harmless changes.** Cases 1, 2 and 4 stay low under both profiles.
- **The clearest gain is the destructive migration (case 5).** `default` described risk in C/systems terms (locks,
  sockets, allocation) and let it through; `web-backend` ranked it at the top and blocked it.
- **Secrets raise the risk score but do not block.** Cases 6 to 8 get risk 1.88 to 1.98 under `web-backend`, but
  `breaking_change` stays near 17%, and a block needs both values above their thresholds. A leaked secret is not a
  contract break, so this is consistent, but the hook must not be relied on to stop secrets from being committed.
- **The profile does not tell a leaked `.env` from an ignored one.** Cases 7 and 8 differ by 0.09 in risk. The model
  sees only the diff text.
- **Case 3 is borderline.** `web-backend` crossed the `breaking_change` threshold (66.0% > 65%) but the risk (1.52)
  was far from 1.85, so it was approved.

## Check: does secret redaction explain the result?

Secret redaction is on by default (`SYSTEMONE_REDACT`, see the README), so in cases 6 and 7 the model received
`postgres://admin:[REDACTED:url_password]@prod/db` instead of the password. To see whether the marker drove the high
`web-backend` risk, cases 6 to 8 were re-run with `SYSTEMONE_REDACT=0` (same session, same repository):

| Case | Profile | risk, redact on → off | breaking, redact on → off | Decision |
|---|---|---|---|---|
| 6 | `default` | 1.06 → 1.08 | 5.5% → 6.0% | approved |
| 6 | `web-backend` | 1.98 → 1.97 | 18.0% → 17.4% | approved |
| 7 | `default` | 0.62 → 0.85 | 5.2% → 5.8% | approved |
| 7 | `web-backend` | 1.97 → 1.98 | 17.0% → 18.3% | approved |
| 8 | `default` | 0.59 → 0.59 | 5.2% → 5.2% | approved |
| 8 | `web-backend` | 1.88 → 1.88 | 15.1% → 15.1% | approved |

- **Redaction does not explain the result.** With the real password visible, the `web-backend` risk moves by at most
  0.01 and nothing changes decision. The model reacts to the context (`DATABASE_URL`, credentials, `.env`), not to the
  password value. The largest change is `default` in case 7 (0.62 → 0.85), still low and without effect on the decision.
- **Case 8 is identical in both modes because its diff contains no password.** The `.env` is ignored, so only `b.py`
  and `.gitignore` are staged. It is a control, not a redaction test.
- The conclusion about secrets stands: they raise the risk score, they do not block, and the profile cannot tell a
  leaked `.env` from an ignored one (1.97 and 1.98 against 1.88).

Still one run per cell, no repetitions.

## Not covered

- Repetitions or any estimate of variance; labels from a second reviewer.
- Realistic diffs (larger, multi-file, framework code); every case here is 1 to 6 lines.
- `tev1:0.8b`, which the README already reports as not discriminating diff risk.
- Secret detection. Use a dedicated scanner (for example gitleaks or detect-secrets) for that; SystemOne Gate is not
  one.

## Reproducing

```bash
D="$(mktemp -d)" || exit 1
export GIT_CEILING_DIRECTORIES="$D"
git -C "$D" init -q
printf 'def total(items):\n    return sum(i.price for i in items)\n' > "$D/a.py"
git -C "$D" add . && git -C "$D" commit -qm init
# stage one change, then compare the profiles:
printf 'ALTER TABLE users DROP COLUMN email;\nDROP TABLE audit_log;\n' > "$D/002_cleanup.sql"
git -C "$D" add 002_cleanup.sql
cd "$D" && for p in default web-backend; do systemone-gate diff --nimble --profile "$p"; done
```
