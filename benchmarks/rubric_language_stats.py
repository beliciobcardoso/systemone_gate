"""Pure statistics helpers for the rubric-language A/B benchmark (stdlib only)."""
import math
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


def argmax_key(probabilities: Mapping[str, float]) -> str:
    """Key with the highest probability; ties resolved by first occurrence."""
    if not probabilities:
        raise ValueError("empty probabilities")
    best_key, best_value = None, -math.inf
    for key, value in probabilities.items():
        if value > best_value:
            best_key, best_value = key, value
    return best_key


def accuracy(y_true: Sequence, y_pred: Sequence) -> float:
    if len(y_true) != len(y_pred):
        raise ValueError("length mismatch")
    if not y_true:
        raise ValueError("empty input")
    return sum(1 for t, p in zip(y_true, y_pred) if t == p) / len(y_true)


def wilson_interval(successes: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion (95% by default)."""
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= successes <= n:
        raise ValueError("successes out of range")
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def confusion_matrix(y_true: Sequence, y_pred: Sequence, labels: Sequence) -> List[List[int]]:
    """Rows = true label, columns = predicted label, in `labels` order."""
    index = {label: i for i, label in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    for t, p in zip(y_true, y_pred):
        if t in index and p in index:
            matrix[index[t]][index[p]] += 1
    return matrix


def macro_f1(y_true: Sequence, y_pred: Sequence, labels: Sequence) -> float:
    """Unweighted mean of per-label F1 (a label with no support and no predictions counts as 0)."""
    if not labels:
        raise ValueError("no labels")
    scores = []
    for label in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == label and p == label)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != label and p == label)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == label and p != label)
        denom = 2 * tp + fp + fn
        scores.append(0.0 if denom == 0 else 2 * tp / denom)
    return sum(scores) / len(scores)


def mean_absolute_error(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    if len(y_true) != len(y_pred) or not y_true:
        raise ValueError("length mismatch or empty input")
    return sum(abs(t - p) for t, p in zip(y_true, y_pred)) / len(y_true)


def mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def paired_counts(correct_a: Sequence[bool], correct_b: Sequence[bool]) -> Dict[str, int]:
    """Paired outcome counts for the same cases under conditions A and B."""
    if len(correct_a) != len(correct_b):
        raise ValueError("length mismatch")
    counts = {"both_right": 0, "only_a_right": 0, "only_b_right": 0, "both_wrong": 0}
    for a, b in zip(correct_a, correct_b):
        if a and b:
            counts["both_right"] += 1
        elif a:
            counts["only_a_right"] += 1
        elif b:
            counts["only_b_right"] += 1
        else:
            counts["both_wrong"] += 1
    return counts


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Exact two-sided McNemar p-value: binomial(n=b+c, 0.5) on the discordant pairs."""
    if only_a < 0 or only_b < 0:
        raise ValueError("counts must be non-negative")
    n = only_a + only_b
    if n == 0:
        return 1.0
    k = min(only_a, only_b)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)
