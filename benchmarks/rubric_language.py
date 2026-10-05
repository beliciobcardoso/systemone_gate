"""A/B benchmark: Portuguese (production) vs English rubrics (experiment RSK-02 / S3).

Usage:
  python benchmarks/rubric_language.py [--models tev1:0.8b nimble:latest] [--tasks triage diff]
                                       [--limit N] [--output PATH] [--dry-run] [--summarize-only]

Sequential requests against a local Ollama /v1/systemone. `--dry-run` validates data files and
rubrics without calling Ollama. `--summarize-only` recomputes the summary from the saved results.
Does not touch production code or defaults.
"""

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import platform
import random
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import rubric_language_stats as st  # noqa: E402

from systemone_gate.client import SystemOneClient  # noqa: E402
from systemone_gate.rubrics import get_diff_rubric, get_triage_rubric  # noqa: E402

DATA_DIR = os.path.join(HERE, "data")
DEFAULT_OUTPUT = os.path.join(HERE, "results", "rubric_language.json")
DEFAULT_MODELS = ["tev1:0.8b", "nimble:latest"]
LANGS = ("pt", "en")
TASKS = ("triage", "diff")
RISK_LABELS = ["0", "1", "2"]
BC_LABELS = ["safe", "potential_break", "breaking_change"]
TIMEOUT_S = 120.0
DETERMINISM_SAMPLE = 5
SEED = 20261005


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_data():
    with open(os.path.join(DATA_DIR, "triage_cases.json"), encoding="utf-8") as f:
        triage = json.load(f)
    with open(os.path.join(DATA_DIR, "diff_cases.json"), encoding="utf-8") as f:
        diffs = json.load(f)
    return triage, diffs


def load_rubrics():
    en = _load_module("rubrics_en", os.path.join(DATA_DIR, "rubrics_en.py"))
    return {
        ("triage", "pt"): get_triage_rubric("default"),
        ("triage", "en"): en.RUBRIC_ERROR_TRIAGE_EN,
        ("diff", "pt"): get_diff_rubric("default"),
        ("diff", "en"): en.RUBRIC_DIFF_RISK_EN,
    }


def validate(triage, diffs, rubrics):
    """Raises AssertionError on any inconsistency between data and rubrics."""
    choices = list(rubrics[("triage", "pt")]["root_cause"]["criteria"].keys())
    for case in triage:
        assert case["label"] in choices, case["id"]
        assert case["state"].strip(), case["id"]
    for label in choices:
        assert sum(1 for c in triage if c["label"] == label) == 5, label
    for case in diffs:
        assert case["risk_level"] in (0, 1, 2), case["id"]
        assert case["breaking_change"] in BC_LABELS, case["id"]
        assert len(case["state"].splitlines()) <= 25, case["id"]
    for lvl in (0, 1, 2):
        assert sum(1 for c in diffs if c["risk_level"] == lvl) == 12, lvl
    for task in TASKS:
        pt, en = rubrics[(task, "pt")], rubrics[(task, "en")]
        assert list(pt) == list(en), task
        for key in pt:
            assert pt[key]["type"] == en[key]["type"]
            if pt[key]["type"] == "choice":
                assert list(pt[key]["criteria"]) == list(en[key]["criteria"])
            else:
                assert len(pt[key]["criteria"]) == len(en[key]["criteria"])


def compact(answers):
    out = {}
    for key, ans in answers.items():
        item = {"type": ans.get("type"), "probabilities": ans.get("probabilities"), "confidence": ans.get("confidence")}
        if "choice" in ans:
            item["choice"] = ans["choice"]
        if "score" in ans:
            item["score"] = ans["score"]
        out[key] = item
    return out


def run_condition(client, model, rubric, cases, label):
    results = []
    for i, case in enumerate(cases, 1):
        start = time.perf_counter()
        resp = client.evaluate(state=case["state"], questions=rubric, model=model)
        ms = round((time.perf_counter() - start) * 1000, 1)
        if "error" in resp:
            print(f"  [{label}] {i}/{len(cases)} {case['id']} ERROR {resp['error'][:120]}", flush=True)
            results.append({"id": case["id"], "error": resp["error"], "status": resp.get("status"), "ms": ms})
        else:
            results.append(
                {
                    "id": case["id"],
                    "answers": compact(resp["answers"]),
                    "input_tokens": resp.get("usage", {}).get("input_tokens"),
                    "ms": ms,
                }
            )
            if i % 10 == 0 or i == len(cases):
                print(f"  [{label}] {i}/{len(cases)}", flush=True)
    return results


def condition_key(task, lang, model):
    return f"{task}|{lang}|{model}"


def _ok(results):
    return {r["id"]: r for r in results if "answers" in r}


def triage_predictions(results, cases):
    ok = _ok(results)
    rows = []
    for case in cases:
        r = ok.get(case["id"])
        if r is None:
            continue
        a = r["answers"]["root_cause"]
        rows.append(
            {
                "id": case["id"],
                "true": case["label"],
                "pred": st.argmax_key(a["probabilities"]),
                "choice": a.get("choice"),
                "confidence": a.get("confidence"),
            }
        )
    return rows


def diff_predictions(results, cases):
    ok = _ok(results)
    rows = []
    for case in cases:
        r = ok.get(case["id"])
        if r is None:
            continue
        risk = r["answers"]["risk_level"]
        bc = r["answers"]["breaking_change"]
        rows.append(
            {
                "id": case["id"],
                "true_risk": str(case["risk_level"]),
                "pred_risk": st.argmax_key(risk["probabilities"]),
                "score": risk.get("score"),
                "risk_confidence": risk.get("confidence"),
                "true_bc": case["breaking_change"],
                "pred_bc": st.argmax_key(bc["probabilities"]),
                "bc_choice": bc.get("choice"),
                "bc_confidence": bc.get("confidence"),
            }
        )
    return rows


def metric_block(y_true, y_pred, labels, confidences):
    n = len(y_true)
    if n == 0:
        return {"n": 0}
    k = sum(1 for t, p in zip(y_true, y_pred) if t == p)
    low, high = st.wilson_interval(k, n)
    return {
        "n": n,
        "correct": k,
        "accuracy": k / n,
        "wilson95": [low, high],
        "macro_f1": st.macro_f1(y_true, y_pred, labels),
        "mean_confidence": st.mean([c for c in confidences if c is not None]),
        "labels": list(labels),
        "confusion": st.confusion_matrix(y_true, y_pred, labels),
    }


def summarize(runs, triage, diffs, models):
    summary = {"conditions": {}, "paired": {}}
    triage_labels = list(get_triage_rubric("default")["root_cause"]["criteria"].keys())
    rows_by_cond = {}
    for key, results in runs.items():
        task, lang, model = key.split("|", 2)
        if task == "triage":
            rows = triage_predictions(results, triage)
            block = metric_block(
                [r["true"] for r in rows], [r["pred"] for r in rows], triage_labels, [r["confidence"] for r in rows]
            )
            block["choice_agrees_with_argmax"] = sum(1 for r in rows if r["choice"] == r["pred"])
            block["failed_requests"] = len(results) - len(rows)
            summary["conditions"][key] = {"root_cause": block}
        else:
            rows = diff_predictions(results, diffs)
            risk = metric_block(
                [r["true_risk"] for r in rows],
                [r["pred_risk"] for r in rows],
                RISK_LABELS,
                [r["risk_confidence"] for r in rows],
            )
            if rows:
                risk["mae_score"] = st.mean_absolute_error(
                    [float(r["true_risk"]) for r in rows], [r["score"] for r in rows]
                )
            bc = metric_block(
                [r["true_bc"] for r in rows],
                [r["pred_bc"] for r in rows],
                BC_LABELS,
                [r["bc_confidence"] for r in rows],
            )
            bc["choice_agrees_with_argmax"] = sum(1 for r in rows if r["bc_choice"] == r["pred_bc"])
            bc["failed_requests"] = len(results) - len(rows)
            summary["conditions"][key] = {"risk_level": risk, "breaking_change": bc}
        rows_by_cond[key] = rows
    for model in models:
        for task, fields in (
            ("triage", [("root_cause", "true", "pred")]),
            ("diff", [("risk_level", "true_risk", "pred_risk"), ("breaking_change", "true_bc", "pred_bc")]),
        ):
            kpt, ken = condition_key(task, "pt", model), condition_key(task, "en", model)
            if kpt not in rows_by_cond or ken not in rows_by_cond:
                continue
            pt_rows = {r["id"]: r for r in rows_by_cond[kpt]}
            en_rows = {r["id"]: r for r in rows_by_cond[ken]}
            ids = [i for i in pt_rows if i in en_rows]
            for name, tcol, pcol in fields:
                a = [pt_rows[i][tcol] == pt_rows[i][pcol] for i in ids]
                b = [en_rows[i][tcol] == en_rows[i][pcol] for i in ids]
                counts = st.paired_counts(a, b)
                p = st.mcnemar_exact(counts["only_a_right"], counts["only_b_right"])
                summary["paired"][f"{task}:{name}|{model}"] = {
                    "n_pairs": len(ids),
                    "pt_acc": sum(a) / len(a) if a else None,
                    "en_acc": sum(b) / len(b) if b else None,
                    "both_right": counts["both_right"],
                    "only_pt_right": counts["only_a_right"],
                    "only_en_right": counts["only_b_right"],
                    "both_wrong": counts["both_wrong"],
                    "mcnemar_exact_p": p,
                }
    return summary


def print_summary(summary):
    print("\n=== Conditions ===")
    for key, blocks in summary["conditions"].items():
        for name, b in blocks.items():
            if not b.get("n"):
                print(f"{key} {name}: no data")
                continue
            extra = f" MAE={b['mae_score']:.3f}" if "mae_score" in b else ""
            print(
                f"{key:28} {name:16} n={b['n']} acc={b['accuracy']:.3f} "
                f"CI95=[{b['wilson95'][0]:.3f},{b['wilson95'][1]:.3f}] macroF1={b['macro_f1']:.3f} "
                f"conf={b['mean_confidence']:.3f}{extra}"
            )
    print("\n=== Paired pt vs en ===")
    for key, p in summary["paired"].items():
        print(
            f"{key:40} pt={p['pt_acc']:.3f} en={p['en_acc']:.3f} both_right={p['both_right']} "
            f"only_pt={p['only_pt_right']} only_en={p['only_en_right']} both_wrong={p['both_wrong']} "
            f"p={p['mcnemar_exact_p']:.4f}"
        )


def determinism_check(client, models, rubrics, triage, diffs, runs):
    """Re-sends 5 random pt requests per model and compares probabilities exactly."""
    rng = random.Random(SEED)
    report = []
    for model in models:
        pool = [("triage", c) for c in triage] + [("diff", c) for c in diffs]
        for task, case in rng.sample(pool, DETERMINISM_SAMPLE):
            first = next((r for r in runs.get(condition_key(task, "pt", model), []) if r["id"] == case["id"]), None)
            if not first or "answers" not in first:
                continue
            resp = client.evaluate(state=case["state"], questions=rubrics[(task, "pt")], model=model)
            if "error" in resp:
                report.append({"model": model, "id": case["id"], "identical": None, "error": resp["error"]})
                continue
            same = all(
                first["answers"][k]["probabilities"] == v["probabilities"] for k, v in compact(resp["answers"]).items()
            )
            report.append({"model": model, "id": case["id"], "task": task, "identical": same})
    return report


def environment_meta(models):
    def sh(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception as exc:  # noqa: BLE001 - metadata is best effort
            return f"unavailable: {exc}"

    try:
        with urllib.request.urlopen("http://localhost:11434/api/version", timeout=5) as r:
            api_version = json.loads(r.read().decode())["version"]
    except Exception as exc:  # noqa: BLE001
        api_version = f"unavailable: {exc}"
    files = {}
    for name in ("triage_cases.json", "diff_cases.json", "rubrics_en.py"):
        with open(os.path.join(DATA_DIR, name), "rb") as f:
            files[name] = hashlib.sha256(f.read()).hexdigest()[:16]
    return {
        "date": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "models": models,
        "ollama_version_cli": sh(["ollama", "-v"]),
        "ollama_api_version": api_version,
        "ollama_list": sh(["ollama", "list"]),
        "gpu": sh(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"]),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpus": os.cpu_count(),
        "git_head": sh(["git", "-C", ROOT, "rev-parse", "HEAD"]),
        "data_sha256_16": files,
        "request_policy": "sequential, one pass per condition, redact=False, default rubric profile",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--tasks", nargs="+", choices=TASKS, default=list(TASKS))
    ap.add_argument("--limit", type=int, default=None, help="use only the first N cases per task (smoke tests)")
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summarize-only", action="store_true")
    args = ap.parse_args(argv)

    triage, diffs = load_data()
    rubrics = load_rubrics()
    validate(triage, diffs, rubrics)
    if args.limit:
        triage, diffs = triage[: args.limit], diffs[: args.limit]
    if args.dry_run:
        n_cases = (len(triage) if "triage" in args.tasks else 0) + (len(diffs) if "diff" in args.tasks else 0)
        n_requests = len(args.models) * 2 * n_cases
        print(
            f"dry-run OK: {len(triage)} triage cases, {len(diffs)} diff cases, rubrics pt/en consistent; "
            f"{len(args.models)} models x {len(args.tasks)} tasks x 2 langs = "
            f"{len(args.models) * len(args.tasks) * 2} conditions, "
            f"{n_requests} requests"
        )
        return 0
    if args.summarize_only:
        with open(args.output, encoding="utf-8") as f:
            saved = json.load(f)
        summary = summarize(saved["runs"], load_data()[0], load_data()[1], saved["meta"]["models"])
        print_summary(summary)
        return 0

    client = SystemOneClient(timeout=TIMEOUT_S, redact=False)
    data = {"triage": triage, "diff": diffs}
    runs = {}
    for model in args.models:
        for task in args.tasks:
            for lang in LANGS:
                key = condition_key(task, lang, model)
                print(f"== {key} ({len(data[task])} cases)", flush=True)
                runs[key] = run_condition(client, model, rubrics[(task, lang)], data[task], key)
    det = (
        determinism_check(client, args.models, rubrics, triage, diffs, runs)
        if "triage" in args.tasks and "diff" in args.tasks
        else []
    )
    summary = summarize(runs, triage, diffs, args.models)
    print_summary(summary)
    print("\ndeterminism:", json.dumps(det))
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(
            {"meta": environment_meta(args.models), "runs": runs, "determinism": det, "summary": summary},
            f,
            ensure_ascii=False,
            indent=1,
        )
    print(f"saved {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
