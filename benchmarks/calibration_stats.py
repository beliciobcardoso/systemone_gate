"""Pure statistics helpers for the threshold calibration analysis (stdlib only)."""

import hashlib
import math
from typing import Dict, List, Optional, Sequence, Tuple


def auc(positive_scores: Sequence[float], negative_scores: Sequence[float]) -> Optional[float]:
    """Probability that a positive scores higher than a negative (ties count half): the area under the
    ROC curve, computed as the Mann-Whitney statistic. 0.5 means the score does not discriminate.
    None when either class is empty."""
    if not positive_scores or not negative_scores:
        return None
    wins = 0.0
    for pos in positive_scores:
        for neg in negative_scores:
            wins += 1.0 if pos > neg else 0.5 if pos == neg else 0.0
    return wins / (len(positive_scores) * len(negative_scores))


def threshold_candidates(values: Sequence[float], max_candidates: Optional[int] = None) -> List[float]:
    """Cut points ``t`` for a rule ``value > t``: one below the smallest value, the midpoint between each
    pair of consecutive distinct values, and one above the largest. Every distinct decision the rule can
    make on ``values`` is reachable. Non-finite values are ignored. With ``max_candidates`` the list is
    thinned evenly, always keeping both ends."""
    if max_candidates is not None and max_candidates < 2:
        raise ValueError("max_candidates deve ser >= 2")
    distinct = sorted({float(v) for v in values if math.isfinite(v)})
    if not distinct:
        return []
    cuts = [distinct[0] - 1.0]
    cuts.extend((low + high) / 2 for low, high in zip(distinct, distinct[1:]))
    cuts.append(distinct[-1] + 1.0)
    if max_candidates is None or len(cuts) <= max_candidates:
        return cuts
    last = len(cuts) - 1
    picks = sorted({round(i * last / (max_candidates - 1)) for i in range(max_candidates)})
    return [cuts[i] for i in picks]


def stratified_folds(items: Sequence[Tuple[str, str]], k: int) -> Dict[str, int]:
    """Deterministic fold (0..k-1) for each ``(id, stratum)``. Within a stratum the ids are ordered by a
    hash and dealt round-robin with a counter that continues across strata, so every fold gets a similar
    share of each stratum, fold sizes differ by at most one, and the result does not depend on the input
    order or on a random seed."""
    if k < 2:
        raise ValueError("k deve ser >= 2")
    ids = [item_id for item_id, _ in items]
    if len(set(ids)) != len(ids):
        raise ValueError("ids duplicados")
    by_stratum: Dict[str, List[str]] = {}
    for item_id, stratum in items:
        by_stratum.setdefault(stratum, []).append(item_id)
    folds: Dict[str, int] = {}
    dealt = 0  # one running counter across strata keeps the fold sizes within one of each other
    for stratum in sorted(by_stratum):
        members = by_stratum[stratum]
        ordered = sorted(members, key=lambda i: hashlib.sha256(i.encode("utf-8")).hexdigest())
        for item_id in ordered:
            folds[item_id] = dealt % k
            dealt += 1
    return folds
