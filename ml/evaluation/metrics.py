"""Window-level accuracy metrics (PLAN.md Section 13.1): FAR, FRR, EER, ROC/DET.

All functions here operate on **calibrated percentile scores** (0-100,
higher = more normal, per ``ml/calibration/percentile.py``'s convention),
so a single shared decision rule applies uniformly across users and models:
accept (treat as genuine) iff ``percentile_score >= threshold``.

Metrics are reported "at both window level (raw model performance) and
decision level (post-smoothing system performance)" per PLAN.md Section
13.1 -- decision-level metrics require the risk engine (T-013, Joel) and
are out of scope for this module, which implements window-level metrics
only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FarFrr:
    threshold: float
    far: float
    frr: float


def compute_far_frr(
    genuine_scores: np.ndarray, impostor_scores: np.ndarray, threshold: float
) -> FarFrr:
    """FAR = fraction of impostor windows accepted; FRR = fraction of genuine windows rejected."""
    genuine_scores = np.asarray(genuine_scores, dtype=float)
    impostor_scores = np.asarray(impostor_scores, dtype=float)
    if len(genuine_scores) == 0:
        raise ValueError("genuine_scores must be non-empty")
    if len(impostor_scores) == 0:
        raise ValueError("impostor_scores must be non-empty")

    far = float(np.mean(impostor_scores >= threshold))
    frr = float(np.mean(genuine_scores < threshold))
    return FarFrr(threshold=threshold, far=far, frr=frr)


@dataclass(frozen=True)
class RocCurve:
    thresholds: np.ndarray
    far: np.ndarray  # a.k.a. false positive rate
    frr: np.ndarray  # a.k.a. false negative rate = 1 - true positive rate


def compute_roc_det_curve(
    genuine_scores: np.ndarray,
    impostor_scores: np.ndarray,
    *,
    n_thresholds: int = 1001,
) -> RocCurve:
    """FAR/FRR swept over a threshold grid spanning the observed score range.

    The same (threshold, far, frr) triples serve both an ROC-style plot
    (far vs. 1-frr) and a DET-style plot (far vs. frr on a normal-deviate
    axis) -- axis choice is a plotting concern, not a data concern.
    """
    genuine_scores = np.asarray(genuine_scores, dtype=float)
    impostor_scores = np.asarray(impostor_scores, dtype=float)
    lo = float(min(genuine_scores.min(), impostor_scores.min()))
    hi = float(max(genuine_scores.max(), impostor_scores.max()))
    if lo == hi:
        hi = lo + 1e-9  # degenerate single-value case: avoid a zero-width grid
    thresholds = np.linspace(lo, hi, n_thresholds)

    far = np.array([np.mean(impostor_scores >= t) for t in thresholds])
    frr = np.array([np.mean(genuine_scores < t) for t in thresholds])
    return RocCurve(thresholds=thresholds, far=far, frr=frr)


@dataclass(frozen=True)
class EerResult:
    eer: float
    threshold: float


def compute_eer(
    genuine_scores: np.ndarray, impostor_scores: np.ndarray, *, n_thresholds: int = 2001
) -> EerResult:
    """Equal Error Rate: the threshold where FAR and FRR are (approximately) equal.

    Reports the average of FAR/FRR at the crossing point, and the
    threshold at which that crossing occurs, as is standard practice.
    """
    curve = compute_roc_det_curve(genuine_scores, impostor_scores, n_thresholds=n_thresholds)
    diffs = np.abs(curve.far - curve.frr)
    idx = int(np.argmin(diffs))
    eer = float((curve.far[idx] + curve.frr[idx]) / 2.0)
    return EerResult(eer=eer, threshold=float(curve.thresholds[idx]))


@dataclass(frozen=True)
class PerUserMetrics:
    user_id: str
    n_genuine: int
    n_impostor: int
    eer: float
    eer_threshold: float


def per_user_metric_spread(results: list[PerUserMetrics]) -> dict[str, float]:
    """PLAN.md Section 13.5: report per-user metric *distributions*, not just the mean."""
    eers = np.array([r.eer for r in results], dtype=float)
    if len(eers) == 0:
        raise ValueError("results must be non-empty")
    return {
        "mean": float(np.mean(eers)),
        "std": float(np.std(eers)),
        "min": float(np.min(eers)),
        "max": float(np.max(eers)),
        "median": float(np.median(eers)),
    }


def bootstrap_confidence_interval(
    values: np.ndarray,
    *,
    n_resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 0,
) -> tuple[float, float]:
    """PLAN.md Section 13.5: bootstrap CIs where feasible. Percentile-bootstrap CI on the mean."""
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        raise ValueError("values must be non-empty")
    rng = np.random.default_rng(seed)
    means = np.empty(n_resamples)
    n = len(values)
    for i in range(n_resamples):
        sample = values[rng.integers(0, n, size=n)]
        means[i] = np.mean(sample)
    alpha = (1.0 - confidence) / 2.0
    lo = float(np.quantile(means, alpha))
    hi = float(np.quantile(means, 1.0 - alpha))
    return lo, hi
