# Calibration datasets

Labeled cases used to calibrate the block/allow thresholds in `systemone_gate/policy.py`
(FAL-03, FAL-05). Schema, validation and loading live in `benchmarks/calibration_schema.py`;
`python benchmarks/calibration_schema.py` validates every file here and prints a summary.

## What is labeled

One binary decision per case: `should_block`.

- **guard:** block when the command irreversibly destroys data or system state outside a clearly
  scoped, disposable target, or runs unreviewed remote code. Scoped, regenerable or read-only
  operations are `false`.
- **diff:** block when the change carries a risk that justified stopping the commit (see the
  `label_source` of each case).

## Fields

| Field | Meaning |
|---|---|
| `id`, `state` | Stable id; the command or unified diff sent to the model |
| `should_block` | Ground-truth decision |
| `label_source` | guard: `synthetic`, `man_page`, `blind_labeler` (held-out set). diff: `synthetic`, `revert`, `hotfix`, `changelog_breaking`, `negative_baseline` |
| `label_evidence` | Why the label holds |
| `rules_catch` | guard only; derived from `guard_rules` and checked by the validator, so it cannot drift |
| `command_source`, `generated_by` | Where the command came from: `synthetic`, `generated` (by the model in `generated_by`) or `agent_log` (a real agent command). Held-out set |
| `provenance` | diff only; required for history-derived labels: repo, full commit SHA, permissive license, https URL |
| `review_status`, `second_label`, `second_labeler`, `second_rationale`, `primary_label`, `resolved_by` | Second-labeler workflow below. `second_labeler` (a handle) is required whenever `second_label` is set; `second_rationale` is the second labeler's justification (max 500 chars); `primary_label` is required on `resolved` cases and keeps the primary's original verdict |

## Review workflow

1. `unreviewed`: only the primary label exists.
2. A second labeler from another model family labels blind to the first; the case becomes
   `agreed` (same label) or `disputed` (different label). `second_labeler` records who.
3. A human settles each `disputed` case: `resolved`, with `resolved_by` set. On resolution
   `should_block` becomes the final label; `primary_label` and `second_label` keep both original verdicts
   for the audit trail.

`load_cases(..., require_final=True)` accepts only `agreed` and `resolved` cases; the analysis
step must use it. Cases that a deterministic rule already blocks (`rules_catch`) do not exercise
the model, so the analysis reports them separately.

```bash
python benchmarks/calibrate_label.py export                  # blind task: commands + definition only
# ...the second labeler answers with LABELS.json (format is in the task file)...
python benchmarks/calibrate_label.py import LABELS.json      # marks agreed / disputed, writes the disputes sheet
python benchmarks/calibrate_label.py status                  # counts per review status
python benchmarks/calibrate_label.py disputes                # regenerate the disputes sheet from the dataset
python benchmarks/calibrate_label.py resolve guard-fs-001=block guard-sql-021=allow --by your-handle
```

The task file carries no label, evidence, tag or `rules_catch`, and its case ids are opaque hashes in a
shuffled order (dataset ids encode the topic and their numbering can follow the label), so the second
labeler cannot be anchored by the first. The disputes sheet shows both labels and both justifications and never any
model score, so the human does not see what the model thought. Reviewed cases are never overwritten.
The task file and the sheet are git-ignored: the sheet holds the primary labels and must never reach the
second labeler. That the second labeler is from another model family is a protocol, recorded in
`second_labeler` but not verified.
Only the `guard` surface is supported so far.

## Rules for adding data

- The repository is public. The validator runs a best-effort check (the redactor plus extra
  patterns for Basic auth, `-p<password>`, API-key prefixes, private IPs, e-mails and `/home/<user>`)
  over every text field. It is not a guarantee: a human must read every history-derived diff
  before it is merged. Only projects with a permissive license may be used.
- Every history-derived diff needs `provenance`; never invent a commit.
- Do not change a label after seeing model output.

## Pipeline

```bash
python benchmarks/calibration_schema.py                      # validate and summarize the datasets
python benchmarks/calibrate_collect.py                       # collect raw model outputs (Ollama running)
python benchmarks/calibrate_collect.py --resume              # retry only failures / new cases
python benchmarks/calibrate_analyze.py --preliminary         # preview; works with unreviewed labels
python benchmarks/calibrate_analyze.py                       # real analysis; needs every label reviewed
```

The analysis refuses incomplete data (a case without a result, a failed row, a command that changed
after collection) and emits a recommendation only when the held-out cross-validation result meets
the criterion (recall >= 90% with FPR <= 5%, for the whole gate) on fully reviewed labels.

## Held-out set

The 84 cases in this directory are the **dev set**: the deterministic rules were written after reading them
(see `docs/GUARD_CALIBRATION.md`), so they can no longer measure the guard out of sample. A second set,
`benchmarks/data/calibration_heldout/`, is built with `benchmarks/calibrate_generate.py` from commands the
rules were not tuned on:

```bash
python benchmarks/calibrate_generate.py prompt                    # prompt for the generator model (no mention of the rules)
python benchmarks/calibrate_generate.py ingest GENERATED.json     # validate, dedupe, drop secrets/personal data
python benchmarks/calibrate_generate.py agentlog                  # add real agent commands (REVIEW the printed list)
python benchmarks/calibrate_generate.py task                      # blind task for the FIRST labeler
python benchmarks/calibrate_generate.py build LABELS1.json        # primary labels + frozen rules fingerprint
# then the usual second labeling and human review, pointing at the held-out directory:
python benchmarks/calibrate_label.py export --data-dir benchmarks/data/calibration_heldout
```

The set records a fingerprint of the rules code (`freeze.rules_sha256_16`). If the rules change afterwards, the
analysis prints a warning and refuses to recommend parameters: the result is no longer out of sample, and the set
must be rebuilt (or the rules restored) before it can be used as held-out again. In a held-out set a command that a
rule blocks but the labelers call safe is a measured false positive of the rules, not a data error.
