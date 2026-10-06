"""Compares two raw collections of the same calibration cases (for example the guard rubric in Portuguese
and in English) by the AUC of the `danger_score`, with a paired bootstrap interval for the difference.

Usage:
  python benchmarks/compare_collections.py RAW_A RAW_B [--labels a b] [--data-dir DIR]
                                           [--resamples 2000] [--seed N] [--json PATH]

The AUC is computed over the cases the deterministic rules do not already block (blockable cases that
need the model, plus every safe case): the only ones where the model can change the outcome. The
difference is B minus A. Cases are resampled together in both collections (paired), so the interval
reflects the cases, not the pairing noise. Labels may be unreviewed: this compares two prompts on the
same labels, it does not calibrate anything.
"""

import argparse
import json
import os
import random
import sys
from typing import Any, Dict, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import calibrate_analyze as analyze  # noqa: E402
import calibration_schema as schema  # noqa: E402
import calibration_stats as cstats  # noqa: E402

DEFAULT_RESAMPLES = 2000
DEFAULT_SEED = 20261006
LOWER_Q, UPPER_Q = 0.025, 0.975


def _auc_over(rows: Sequence[analyze.Row], indices: Sequence[int]) -> Optional[float]:
    positives = [rows[i].check.danger_score for i in indices if rows[i].should_block]
    negatives = [rows[i].check.danger_score for i in indices if not rows[i].should_block]
    return cstats.auc(positives, negatives)


def paired_auc_delta(
    rows_a: Sequence[analyze.Row],
    rows_b: Sequence[analyze.Row],
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    """AUC of each collection and the bootstrap interval of AUC_b - AUC_a over the model-dependent cases."""
    if [r.case_id for r in rows_a] != [r.case_id for r in rows_b]:
        raise ValueError("as duas coletas devem cobrir os mesmos casos, na mesma ordem")
    universe = [i for i, row in enumerate(rows_a) if not row.rules_catch]
    auc_a, auc_b = _auc_over(rows_a, universe), _auc_over(rows_b, universe)
    if auc_a is None or auc_b is None:
        raise ValueError("é preciso ter casos bloqueáveis e seguros fora do alcance das regras")
    rng = random.Random(seed)
    deltas: List[float] = []
    for _ in range(resamples):
        sample = [rng.choice(universe) for _ in universe]
        a, b = _auc_over(rows_a, sample), _auc_over(rows_b, sample)
        if a is not None and b is not None:
            deltas.append(b - a)
    deltas.sort()
    return {
        "n": len(universe),
        "auc_a": auc_a,
        "auc_b": auc_b,
        "delta": auc_b - auc_a,
        "ci95": [deltas[int(LOWER_Q * len(deltas))], deltas[min(len(deltas) - 1, int(UPPER_Q * len(deltas)))]],
        "resamples_used": len(deltas),
    }


def _rows(raw_path: str, cases: Sequence[Dict[str, Any]], model: str) -> List[analyze.Row]:
    _, runs = analyze._load_raw(raw_path)
    key = f"{schema.SURFACE_GUARD}|{model}"
    if key not in runs:
        raise analyze.AnalysisError(f"{raw_path}: sem resultados para {model}")
    return analyze.build_rows(model, cases, runs[key])


def compare(raw_a: str, raw_b: str, data_dir: str, resamples: int, seed: int) -> Dict[str, Any]:
    _, runs_a = analyze._load_raw(raw_a)
    _, runs_b = analyze._load_raw(raw_b)
    cases = schema.load_dir(data_dir)[schema.SURFACE_GUARD]
    models = sorted(
        {k.split("|", 1)[1] for k in runs_a if k.startswith("guard|")}
        & {k.split("|", 1)[1] for k in runs_b if k.startswith("guard|")}
    )
    if not models:
        raise analyze.AnalysisError("as coletas não têm nenhum modelo em comum")
    return {
        "unreviewed_cases": sum(1 for c in cases if not schema.is_final(c)),
        "models": {
            m: paired_auc_delta(_rows(raw_a, cases, m), _rows(raw_b, cases, m), resamples, seed) for m in models
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("raw_a")
    ap.add_argument("raw_b")
    ap.add_argument("--labels", nargs=2, default=["A", "B"], metavar=("A", "B"))
    ap.add_argument("--data-dir", default=schema.DEFAULT_DATA_DIR)
    ap.add_argument("--resamples", type=int, default=DEFAULT_RESAMPLES)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    if args.resamples < 100:
        ap.error("--resamples deve ser >= 100")
    try:
        result = compare(args.raw_a, args.raw_b, args.data_dir, args.resamples, args.seed)
    except (analyze.AnalysisError, schema.CalibrationDataError, ValueError, OSError) as exc:
        print(f"recusado: {exc}", file=sys.stderr)
        return 1
    a, b = args.labels
    if result["unreviewed_cases"]:
        print(f"aviso: {result['unreviewed_cases']} rótulos sem revisão humana; compara dois prompts, não calibra")
    for model, m in result["models"].items():
        low, high = m["ci95"]
        print(
            f"{model}: AUC {a} {m['auc_a']:.3f} | {b} {m['auc_b']:.3f} | "
            f"delta ({b} - {a}) {m['delta']:+.3f} IC95% [{low:+.3f}, {high:+.3f}] n={m['n']}"
        )
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
