"""Latency benchmark for SystemOne Gate (stdlib-only, prints results only).

Usage: python benchmarks/latency.py [--models tev1:0.8b nimble:latest] [-n 30]
                                    [--warmup 2] [--cold] [--determinism 20]

--cold runs `ollama stop <model>` (unloads the model) before timing one call.
Requires a running Ollama with /v1/systemone. Run on an otherwise idle machine.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from systemone_gate.client import SystemOneClient  # noqa: E402
from systemone_gate.guard_rules import evaluate_command  # noqa: E402
from systemone_gate.rubrics import RUBRIC_COMMAND_SAFETY, RUBRIC_DIFF_RISK  # noqa: E402

SHORT_COMMAND = "find /var/log/app -name '*.log' -mtime +7 -exec rm {} +"


def build_diff(lines: int = 100) -> str:
    # The endpoint rejects inputs above ~2050 tokens, so the diff lines are short.
    header = "diff --git a/src/pool.c b/src/pool.c\n--- a/src/pool.c\n+++ b/src/pool.c\n@@ -10,6 +10,%d @@\n" % lines
    body = []
    for i in range(lines):
        body.append(f"+  s[{i}] = 0;")
    return header + "\n".join(body) + "\n"


def percentile(values, pct):
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def timed_call(client, model, state, questions):
    start = time.perf_counter()
    result = client.evaluate(state=state, questions=questions, model=model)
    elapsed_ms = (time.perf_counter() - start) * 1000
    if "error" in result:
        raise RuntimeError(result["error"])
    return elapsed_ms, result


def summarize(label, model, samples):
    print(f"{model:14} {label:10} n={len(samples):<3} min={min(samples):7.1f} "
          f"p50={statistics.median(samples):7.1f} p95={percentile(samples, 95):7.1f} max={max(samples):7.1f} ms")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=["tev1:0.8b", "nimble:latest"])
    parser.add_argument("-n", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--cold", action="store_true", help="unload each model first and time one cold call")
    parser.add_argument("--determinism", type=int, default=0, metavar="N",
                        help="repeat one request N times and compare probabilities")
    args = parser.parse_args()

    client = SystemOneClient(timeout=120)
    payloads = {"guard": (SHORT_COMMAND, RUBRIC_COMMAND_SAFETY), "diff-100": (build_diff(), RUBRIC_DIFF_RISK)}

    for model in args.models:
        if args.cold:
            subprocess.run(["ollama", "stop", model], check=False, capture_output=True)
            time.sleep(2)
            ms, _ = timed_call(client, model, *payloads["guard"])
            print(f"{model:14} cold-start first call: {ms:.1f} ms")
        for label, (state, questions) in payloads.items():
            for _ in range(args.warmup):
                timed_call(client, model, state, questions)
            samples = [timed_call(client, model, state, questions)[0] for _ in range(args.n)]
            summarize(label, model, samples)
        if args.determinism:
            state, questions = payloads["guard"]
            seen = {json.dumps(timed_call(client, model, state, questions)[1].get("answers"), sort_keys=True)
                    for _ in range(args.determinism)}
            print(f"{model:14} determinism: {len(seen)} distinct answer payload(s) in {args.determinism} calls")

    calls = 1000
    start = time.perf_counter()
    for _ in range(calls):
        evaluate_command("rm -rf /")
    per_call_us = (time.perf_counter() - start) / calls * 1e6
    print(f"rules layer    evaluate_command: {per_call_us:.1f} us/call over {calls} calls")


if __name__ == "__main__":
    main()
