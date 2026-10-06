"""Collects raw model outputs for the calibration datasets (step 2 of the threshold calibration).

Usage:
  python benchmarks/calibrate_collect.py [--models tev1:0.8b nimble:latest] [--surfaces guard diff]
                                         [--data-dir DIR] [--output PATH] [--limit N] [--timeout S]
                                         [--endpoint URL] [--resume] [--dry-run]

Sends every case to a local Ollama /v1/systemone with the production rubric and saves the answers
(scores, probabilities, confidence) so the analysis step can sweep thresholds offline, without
calling the model again. Collection does not need reviewed labels and never reads them; the
analysis step joins by case id and refuses results whose command/diff changed (`state_sha256_16`).

The output file is written atomically after every case. An existing output is never overwritten
silently: use `--resume` (keeps every other condition and case, reuses only successful results
whose case is unchanged, and refuses to continue when the model digest or a rubric differs from
the saved run) or `--overwrite` to start over.

Cases that a deterministic guard rule already blocks are sent to the model too: the analysis
separates them with `rules_catch`. Requests are strictly sequential. Exit code is 1 when any
request failed (the failures are saved so `--resume` retries only them).
"""

import argparse
import datetime
import hashlib
import http.client
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import calibration_schema as schema  # noqa: E402
from rubric_language import compact  # noqa: E402

from systemone_gate.client import DEFAULT_ENDPOINT, SystemOneClient  # noqa: E402
from systemone_gate.rubrics import RUBRIC_COMMAND_SAFETY, get_diff_rubric  # noqa: E402

OUTPUT_VERSION = 1
DEFAULT_OUTPUT = os.path.join(HERE, "results", "calibration_raw.json")
DEFAULT_MODELS = ["tev1:0.8b", "nimble:latest"]
# The first call after an idle period loads the model (12 to 72 s measured for nimble).
DEFAULT_TIMEOUT_S = 120.0
META_TIMEOUT_S = 5
HASH_LEN = 16


def state_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_LEN]


def rubric_for(surface: str) -> Dict[str, Any]:
    """The rubric production sends for this surface (default profile)."""
    if surface == schema.SURFACE_GUARD:
        return RUBRIC_COMMAND_SAFETY
    if surface == schema.SURFACE_DIFF:
        return get_diff_rubric("default")
    raise ValueError(f"surface desconhecida: {surface!r}")


def condition_key(surface: str, model: str) -> str:
    return f"{surface}|{model}"


def rubric_hash(surface: str) -> str:
    return state_hash(json.dumps(rubric_for(surface), sort_keys=True, ensure_ascii=False))


def _answers_problem(answers: Any, rubric: Dict[str, Any]) -> Optional[str]:
    """Why ``answers`` is not a complete answer to ``rubric``, or None when it is."""
    if not isinstance(answers, dict):
        return "campo 'answers' ausente ou não é objeto"
    missing = [key for key in rubric if not isinstance(answers.get(key), dict)]
    return f"respostas ausentes para {missing}" if missing else None


def _response_problem(resp: Any, rubric: Dict[str, Any]) -> Optional[str]:
    if not isinstance(resp, dict):
        return "resposta do endpoint não é um objeto JSON"
    return _answers_problem(resp.get("answers"), rubric)


def _reusable(row: Optional[Dict[str, Any]], case_hash: str, rubric: Dict[str, Any]) -> bool:
    return (
        row is not None
        and row.get("state_sha256_16") == case_hash
        and _answers_problem(row.get("answers"), rubric) is None
    )


def merge_rows(previous: Optional[Sequence[Dict[str, Any]]], new: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rows already saved, with ``new`` replacing the ones of the same id; ids not in ``new`` are kept."""
    merged = {row["id"]: row for row in previous or []}
    merged.update({row["id"]: row for row in new})
    return list(merged.values())


def collect_condition(
    client: SystemOneClient,
    model: str,
    surface: str,
    cases: Sequence[Dict[str, Any]],
    previous: Optional[Sequence[Dict[str, Any]]] = None,
    log: Callable[[str], None] = print,
    on_result: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
) -> List[Dict[str, Any]]:
    """One result per case, in case order. ``previous`` results that succeeded and still match the
    case are reused; failed or stale ones are collected again. ``on_result`` receives the rows so far after
    each case."""
    rubric = rubric_for(surface)
    old = {row["id"]: row for row in previous or []}
    results: List[Dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        case_hash = state_hash(case["state"])
        if _reusable(old.get(case["id"]), case_hash, rubric):
            results.append(old[case["id"]])
            continue
        start = time.perf_counter()
        resp = client.evaluate(state=case["state"], questions=rubric, model=model)
        ms = round((time.perf_counter() - start) * 1000, 1)
        row: Dict[str, Any] = {"id": case["id"], "state_sha256_16": case_hash, "ms": ms}
        if isinstance(resp, dict) and "error" in resp:
            row.update(error=resp["error"], error_kind=resp.get("error_kind"), status=resp.get("status"))
        elif (problem := _response_problem(resp, rubric)) is not None:
            row.update(error=problem, error_kind="invalid_response")
        else:
            usage = resp.get("usage")
            tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
            row.update(answers=compact(resp["answers"]), input_tokens=tokens)
        if "error" in row:
            log(f"  [{condition_key(surface, model)}] {index}/{len(cases)} {case['id']} ERROR {row['error'][:120]}")
        elif index % 10 == 0 or index == len(cases):
            log(f"  [{condition_key(surface, model)}] {index}/{len(cases)}")
        results.append(row)
        if on_result is not None:
            on_result(list(results))
    return results


def _http_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=META_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _git_head() -> str:
    try:
        out = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unavailable"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable: {exc}"


def _git_dirty() -> Optional[bool]:
    """True when the package code has uncommitted changes (HEAD alone then does not identify the code)."""
    try:
        out = subprocess.run(
            ["git", "-C", ROOT, "status", "--porcelain", "--", "systemone_gate"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(out.stdout.strip()) if out.returncode == 0 else None


def collect_meta(
    client: SystemOneClient,
    models: Sequence[str],
    data_hashes: Dict[str, str],
    surfaces: Sequence[str] = (schema.SURFACE_GUARD, schema.SURFACE_DIFF),
) -> Dict[str, Any]:
    """Environment of the run. Ollama is queried over HTTP only and every field is best effort, so a
    missing server never aborts a collection. The model digest pins which weights were measured:
    thresholds calibrated for one digest are not valid for another."""
    parts = urllib.parse.urlsplit(client.endpoint)
    base = f"{parts.scheme}://{parts.netloc}"
    try:
        api_version: Any = _http_json(f"{base}/api/version")["version"]
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException) as exc:
        api_version = f"unavailable: {exc}"
    digests: Dict[str, Optional[str]] = {model: None for model in models}
    try:
        for entry in _http_json(f"{base}/api/tags")["models"]:
            if entry.get("name") in digests:
                digests[entry["name"]] = entry.get("digest")
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        pass
    return {
        "date": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "models": list(models),
        "model_digests": digests,
        "ollama_api_version": api_version,
        "endpoint": client.endpoint,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "git_head": _git_head(),
        "git_dirty_package": _git_dirty(),
        "timeout_s": client.timeout,
        "rubric_sha256_16": {surface: rubric_hash(surface) for surface in surfaces},
        "data_sha256_16": dict(data_hashes),
        "request_policy": "sequential, one pass per condition, redact=False, default rubric profile",
    }


def _data_hashes(directory: str) -> Dict[str, str]:
    hashes: Dict[str, str] = {}
    for name in sorted(os.listdir(directory)):
        if name.endswith(".json"):
            with open(os.path.join(directory, name), "rb") as handle:
                hashes[name] = hashlib.sha256(handle.read()).hexdigest()[:HASH_LEN]
    return hashes


class PreviousOutputError(ValueError):
    """The saved output cannot be used as a base for ``--resume``."""


def _load_previous(path: str) -> Tuple[Dict[str, Any], Dict[str, List[Dict[str, Any]]]]:
    """Meta and runs of a saved output. Raises PreviousOutputError instead of treating damage as empty."""
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError) as exc:
        raise PreviousOutputError(f"não foi possível ler {path}: {exc}") from None
    if not isinstance(doc, dict) or doc.get("schema_version") != OUTPUT_VERSION:
        raise PreviousOutputError(f"{path}: schema_version diferente de {OUTPUT_VERSION}")
    meta, runs = doc.get("meta"), doc.get("runs")
    valid_runs = isinstance(runs, dict) and all(
        isinstance(rows, list) and all(isinstance(r, dict) and isinstance(r.get("id"), str) for r in rows)
        for rows in runs.values()
    )
    if not isinstance(meta, dict) or not valid_runs:
        raise PreviousOutputError(f"{path}: estrutura de meta/runs inválida")
    return meta, runs


def resume_conflicts(previous: Dict[str, Any], current: Dict[str, Any], models: Sequence[str]) -> List[str]:
    """Reasons the saved results cannot be mixed with a new collection (empty list when compatible).

    Reusing rows measured with other weights or another rubric would make the file claim a provenance
    that is false for them, so any difference, or any digest that cannot be verified, is a conflict."""
    conflicts: List[str] = []
    old_digests = previous.get("model_digests") or {}
    new_digests = current.get("model_digests") or {}
    for model in models:
        old, new = old_digests.get(model), new_digests.get(model)
        if old is None or new is None:
            conflicts.append(f"digest de {model} não verificável (salvo={old!r}, atual={new!r})")
        elif old != new:
            conflicts.append(f"digest de {model} mudou ({old[:12]} -> {new[:12]})")
    old_rubrics = previous.get("rubric_sha256_16") or {}
    for surface, new_hash in (current.get("rubric_sha256_16") or {}).items():
        if surface in old_rubrics and old_rubrics[surface] != new_hash:
            conflicts.append(f"a rubrica de {surface} mudou desde a coleta salva")
    return conflicts


def _save(path: str, meta: Dict[str, Any], runs: Dict[str, List[Dict[str, Any]]]) -> None:
    """Atomic write (temp file + rename): a kill or a full disk never leaves a truncated output."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    document = {"schema_version": OUTPUT_VERSION, "meta": meta, "runs": runs}
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".calibration_raw.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=1)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("deve ser um inteiro >= 1")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--surfaces", nargs="+", choices=schema.SURFACES, default=list(schema.SURFACES))
    ap.add_argument("--data-dir", default=schema.DEFAULT_DATA_DIR)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--limit", type=_positive_int, default=None, help="use only the first N cases per surface")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true", help="keep saved results and collect only what is missing")
    mode.add_argument("--overwrite", action="store_true", help="replace an existing --output")
    ap.add_argument("--dry-run", action="store_true", help="validate the datasets and count requests; send nothing")
    args = ap.parse_args(argv)
    models = list(dict.fromkeys(args.models))

    try:
        grouped = schema.load_dir(args.data_dir)
    except (schema.CalibrationDataError, OSError) as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        return 1
    data = {s: grouped[s][: args.limit] for s in args.surfaces if grouped[s]}
    if not data:
        print(f"nenhum caso nas superfícies {args.surfaces} em {args.data_dir}", file=sys.stderr)
        return 2
    total = len(models) * sum(len(cases) for cases in data.values())
    if args.dry_run:
        counts = ", ".join(f"{s}: {len(c)} cases" for s, c in data.items())
        print(f"dry-run OK: {counts}; {len(models)} models; {total} requests")
        return 0
    if os.path.exists(args.output) and not (args.resume or args.overwrite):
        print(f"{args.output} já existe: use --resume para continuar ou --overwrite para recomeçar", file=sys.stderr)
        return 2

    client = SystemOneClient(endpoint=args.endpoint, timeout=args.timeout, redact=False)
    meta = collect_meta(client, models, _data_hashes(args.data_dir), list(data))
    meta["limit"] = args.limit
    for model, digest in meta["model_digests"].items():
        if digest is None:
            print(f"aviso: {model} não aparece em /api/tags; o digest não será registrado", file=sys.stderr)
    runs: Dict[str, List[Dict[str, Any]]] = {}
    if args.resume and os.path.exists(args.output):
        try:
            previous_meta, runs = _load_previous(args.output)
        except PreviousOutputError as exc:
            print(f"--resume recusado: {exc}", file=sys.stderr)
            return 2
        conflicts = resume_conflicts(previous_meta, meta, models)
        if conflicts:
            detail = "\n  ".join(conflicts)
            print(f"--resume recusado:\n  {detail}\nuse --overwrite para recomeçar", file=sys.stderr)
            return 2
        meta["resumed_from"] = previous_meta.get("date")
    reused = 0
    try:
        for model in models:
            for surface, cases in data.items():
                key = condition_key(surface, model)
                print(f"== {key} ({len(cases)} cases)", flush=True)
                saved = runs.get(key, [])
                base = runs

                def checkpoint(rows, key=key, saved=saved, base=base):
                    _save(args.output, meta, {**base, key: merge_rows(saved, rows)})

                rows = collect_condition(client, model, surface, cases, saved, on_result=checkpoint)
                reused += sum(1 for row in rows if row in saved)
                runs = {**runs, key: merge_rows(saved, rows)}
                _save(args.output, meta, runs)
    except KeyboardInterrupt:
        print(f"\ninterrompido; o progresso até aqui está em {args.output} (retome com --resume)", file=sys.stderr)
        return 130
    failed = sum(1 for rows in runs.values() for row in rows if "error" in row)
    print(f"saved {args.output}: {total - reused} requests sent, {reused} reused, {failed} failed in the file")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
