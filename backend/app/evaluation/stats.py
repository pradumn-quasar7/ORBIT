"""Uncertainty for benchmark results: percentile bootstrap over scenarios.

A metric is a ratio of sums (Σ numerators / Σ denominators) over scenarios, so the
resampling unit is the scenario. Paired comparisons resample the *same* scenario
indices for both variants, which removes between-world variance from the
difference — every variant saw exactly the same generated worlds.
"""
import random
from typing import Dict, List, Optional, Sequence, Tuple

Pair = Tuple[float, float]  # (numerator, denominator) for one scenario


def ratio(pairs: Sequence[Pair]) -> Optional[float]:
    den = sum(d for _, d in pairs)
    return sum(n for n, _ in pairs) / den if den else None


def bootstrap_ci(pairs: Sequence[Pair], n: int = 1000, seed: int = 0, alpha: float = 0.05) -> Dict[str, Optional[float]]:
    est = ratio(pairs)
    if est is None:
        return {"estimate": None, "low": None, "high": None, "scenarios": 0}
    rng = random.Random(seed)
    k = len(pairs)
    samples: List[float] = []
    for _ in range(n):
        value = ratio([pairs[rng.randrange(k)] for _ in range(k)])
        if value is not None:
            samples.append(value)
    samples.sort()
    lo = samples[int((alpha / 2) * (len(samples) - 1))]
    hi = samples[int((1 - alpha / 2) * (len(samples) - 1))]
    return {"estimate": est, "low": lo, "high": hi, "scenarios": sum(1 for _, d in pairs if d)}


def paired_delta(a: Sequence[Pair], b: Sequence[Pair], n: int = 1000, seed: int = 0, alpha: float = 0.05) -> Dict[str, Optional[float]]:
    """CI for ratio(b) − ratio(a) with scenarios resampled jointly."""
    assert len(a) == len(b)
    ra, rb = ratio(a), ratio(b)
    if ra is None or rb is None:
        return {"delta": None, "low": None, "high": None}
    rng = random.Random(seed)
    k = len(a)
    samples: List[float] = []
    for _ in range(n):
        idx = [rng.randrange(k) for _ in range(k)]
        x, y = ratio([a[i] for i in idx]), ratio([b[i] for i in idx])
        if x is not None and y is not None:
            samples.append(y - x)
    samples.sort()
    return {
        "delta": rb - ra,
        "low": samples[int((alpha / 2) * (len(samples) - 1))],
        "high": samples[int((1 - alpha / 2) * (len(samples) - 1))],
    }
