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
| `label_source` | guard: `synthetic`, `man_page`. diff: `synthetic`, `revert`, `hotfix`, `changelog_breaking`, `negative_baseline` |
| `label_evidence` | Why the label holds |
| `rules_catch` | guard only; derived from `guard_rules` and checked by the validator, so it cannot drift |
| `provenance` | diff only; required for history-derived labels: repo, full commit SHA, permissive license, https URL |
| `review_status`, `second_label`, `resolved_by` | Second-labeler workflow below |

## Review workflow

1. `unreviewed`: only the primary label exists.
2. A second labeler from another model family labels blind to the first; the case becomes
   `agreed` (same label) or `disputed` (different label).
3. A human settles each `disputed` case: `resolved`, with `resolved_by` set.

`load_cases(..., require_final=True)` accepts only `agreed` and `resolved` cases; the analysis
step must use it. Cases that a deterministic rule already blocks (`rules_catch`) do not exercise
the model, so the analysis reports them separately.

## Rules for adding data

- The repository is public. The validator runs a best-effort check (the redactor plus extra
  patterns for Basic auth, `-p<password>`, API-key prefixes, private IPs, e-mails and `/home/<user>`)
  over every text field. It is not a guarantee: a human must read every history-derived diff
  before it is merged. Only projects with a permissive license may be used.
- Every history-derived diff needs `provenance`; never invent a commit.
- Do not change a label after seeing model output.
