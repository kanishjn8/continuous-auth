"""Percentile score calibration (PLAN.md Section 10.6).

"Each raw score is converted to a percentile against that user's own
enrollment score distribution. This yields a bounded, per-user-meaningful
score." Calibration parameters (the sorted reference distribution) are
part of the user's profile and are versioned with the model (stored inside
``ModelArtifact``, ``ml/training/common.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class PercentileCalibrator:
    """Maps a raw one-class-model score to a 0-100 percentile.

    Convention: raw scores follow the sklearn ``IsolationForest.score_samples``
    convention -- *higher raw score = more normal*. Consequently the
    calibrated percentile also follows "higher = more normal", so a
    percentile near 0 indicates the current window looks nothing like the
    user's enrollment behavior.
    """

    reference_scores: list[float] = field(default_factory=list)

    @classmethod
    def fit(cls, scores: np.ndarray) -> PercentileCalibrator:
        if len(scores) == 0:
            raise ValueError("cannot fit a percentile calibrator on zero scores")
        return cls(reference_scores=sorted(float(s) for s in scores))

    def transform(self, scores: np.ndarray) -> np.ndarray:
        """Return the percentile rank (0-100) of each score against the
        fitted reference distribution. Monotonic non-decreasing in ``scores``.
        """
        ref = np.asarray(self.reference_scores)
        if len(ref) == 0:
            raise ValueError("calibrator has not been fit")
        ranks = np.searchsorted(ref, np.asarray(scores), side="right")
        return (ranks / len(ref)) * 100.0
