# Guard calibration: what was measured and what the guard relies on

🌐 **English** · [Português (Brasil)](GUARD_CALIBRATION.pt-BR.md)

Short version: the **model verdict is not what makes the guard safe**. The default thresholds were left unchanged
because no setting of either model meets the target; the deterministic rules (`guard_rules`, `guard_ops`) are
the guard, and they were widened to cover the commands the model could not be trusted with.

## Method

- **Dataset:** 84 synthetic shell commands (`benchmarks/data/calibration/guard_synthetic.json`), label
  `should_block`: 39 to block, 45 safe. Every case has a second label produced blind by a model of a different
  family (`gemini-2-5`); all 84 agreed with the primary label (0 disputes). The set is mostly unambiguous, so
  full agreement says little about borderline commands.
- **Target:** recall >= 90% with a false-positive rate (FPR) <= 5% for the whole gate (rules + model), measured
  out of sample (stratified 5-fold cross-validation, thresholds chosen inside each fold), with Wilson 95%
  intervals. The `guard` rubric was the English one shipped in 0.4.0.
- **Models:** `tev1:0.8b` (the production guard model) and `nimble:latest`, collected on 2026-10-06 at commit
  `f41d124` with `benchmarks/calibrate_collect.py`; raw outputs are in `benchmarks/results/calibration_raw.json`.

## Result before the rules were widened

At that commit the rules caught 15 of the 39 commands to block (recall 38.5%, FPR 0%), so 24 depended on the model.

| Scenario | Recall | FPR |
|---|---|---|
| Rules only | 38.5% | 0% |
| `tev1:0.8b`, current defaults (threshold 1.5) | 38.5% | 0% |
| `tev1:0.8b`, best out-of-sample (`score_only` variant) | 64.1% | 6.7% |
| `nimble`, current defaults (threshold 1.5) | 84.6% | 17.8% |
| `nimble`, best out-of-sample | 66.7% | 4.4% |

- `tev1:0.8b` adds nothing at the default threshold (model-only recall 0%) and separates the classes poorly
  (AUC of `danger_score` 0.72).
- `nimble` separates well (AUC 0.91), but no threshold reaches the target: the default blocks 18% of safe commands,
  and a threshold with ~4% FPR only catches two thirds of the dangerous ones. It is also about 2.5x slower.
- Neither model reached the target, so the tool refused to recommend new defaults and **the defaults stay as they are**.

## What changed in the rules

The 24 model-dependent commands were mostly unambiguous destructive operations, which a rule can catch without
a model and without false positives. `guard_ops.py` now blocks, when run without an interactive confirmation:

| Area | Blocked | Still allowed (examples) |
|---|---|---|
| Kubernetes | `delete namespace` of a production-looking name (`prod`, `production`, `prd`) or `--all`; `delete pvc --all` | `delete pod`, `delete namespace preview-123`, `delete pvc data-0` |
| Terraform/OpenTofu | `destroy` / `apply -destroy` with `-auto-approve` | `plan`, `destroy` (prompts) |
| AWS S3 | `rb --force`; recursive `rm` on a bucket root | `rm s3://b/tmp/ --recursive`, `rb` of an empty bucket |
| GCP / Azure | `delete` of sql/projects/group with `--quiet` / `--yes` | the same without the flag (prompts) |
| Docker | `system prune --volumes`; `volume prune -f` | `image prune`, `system prune`, `volume prune` (prompts) |
| Databases | `dropdb`, `pg_dropcluster`, `mysqladmin -f drop`, `redis-cli FLUSHALL/FLUSHDB`, `truncate` of a DB data file | `dropdb -i`, other `redis-cli` commands |
| Accounts / cron | `userdel -r`, `deluser --remove-home`, `crontab -r` | `userdel`, `crontab -l`, `crontab -i -r` |
| Git | `branch -D main/master`; `clean -fx` (ignored files) | `branch -D feature/x`, `clean -fd`, `clean -nfx` |
| Filesystem | `find / ... -delete`, `shred` of keys/system config/devices, `mv x /dev/null`, `rm -rf .git` (the repository's own, not a nested clone), `rm -rf /etc/<x>`, `/boot/<x>`, `/usr/{bin,lib}`, `/var/lib` and `/var/lib/<x>` (not `apt`, `dpkg`, `cloud`), anything under a database data dir | `find ./ -delete`, `shred notes.txt`, `rm -rf node_modules/.git`, `rm -rf /usr/src/*`, `rm -rf /var/lib/apt/lists/*`, `rm -rf /etc/nginx/conf.d/old.conf`, `truncate` of a `*.log` |

Deliberately **not** blocked, because they are common and legitimate in everyday work and a rule would produce
false positives: `git reset --hard`, `docker volume rm <name>`, `kubectl delete namespace` of a non-production
name, `docker compose down -v`. These remain a human decision (or the model's, as a warning). `git clean -fdx` is a judgment call: it is blocked because it deletes ignored files such as `.env`, even though it is also a common CI step (`git clean -fd` is allowed).

The rules still do not replace judgment: they analyze text, not intent, and variable expansion, scripts and
Makefiles are not analyzed (see the `guard_rules` module docstring).

## Result after widening the rules, and its limit

The rules now catch 37 of the 39 commands (recall 94.9%, 95% CI 83.1-98.6%) with 0 false positives in 45
(FPR 0%, 95% CI 0-7.9%). **This is an in-sample number**: the rules were written after reading these cases, and
most of them are the cases themselves. It shows that the rules are consistent with the labels and do not block
the 45 safe commands, not that 95% of real-world destructive commands are caught. The two remaining commands
(`git reset --hard origin/main`, `docker volume rm pgdata`) are the deliberate omissions above.

With only 2 model-dependent cases left, the dataset can no longer calibrate the model: `calibrate_analyze.py`
refuses to recommend (it needs at least 10 such cases) and says the result comes from the rules.

## Next step

Calibrating a model threshold again needs a **held-out set** of commands the rules do not already catch, with
a primary label and a blind second label. Until then the thresholds stay uncalibrated and documented as such.

## Reproducing

```bash
git checkout f41d124                      # dataset and rules as measured
python benchmarks/calibrate_analyze.py --raw benchmarks/results/calibration_raw.json
```
