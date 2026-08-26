"""Zero-effort impostor cross-evaluation (I1, PLAN.md Section 13.2).

"Another enrolled user behaving naturally, not attempting imitation.
Cross-evaluate every user's sessions against every other user's model.
Free from existing data." I1 is mandatory; I2 (informed impostor) and I3
(replay) require human participants / real hardware and are out of scope
for this package (PLAN.md Section 13.2, 13.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ml.features.schema import FeatureWindow
from ml.training.common import ModelArtifact, ModelSchemaMismatchError, score_window


@dataclass
class CrossEvalResult:
    user_id: str
    genuine_scores: list[float] = field(default_factory=list)
    impostor_scores: dict[str, list[float]] = field(default_factory=dict)  # other_user_id -> scores

    def all_impostor_scores(self) -> np.ndarray:
        flat: list[float] = []
        for scores in self.impostor_scores.values():
            flat.extend(scores)
        return np.asarray(flat, dtype=float)


def _modality_scores(artifact: ModelArtifact, windows: list[FeatureWindow]) -> list[float]:
    """Score every window, excluding any that individually fail to score.

    A schema mismatch propagates (PLAN.md guardrail: "refuse, never
    silently use" applies to the whole artifact, not just one window) but
    a non-finite raw score on a single window must not abort scoring for
    every other window/user pair in a cross-evaluation loop -- that one
    degenerate window is excluded, the same way an unavailable modality
    already contributes no row rather than crashing the run.
    """
    scores = []
    for w in windows:
        try:
            result = score_window(artifact, w)
        except ModelSchemaMismatchError:
            raise
        except ValueError:
            continue
        if result.available and result.percentile_score is not None:
            scores.append(result.percentile_score)
    return scores


def zero_effort_cross_evaluation(
    artifacts_by_user: dict[str, ModelArtifact],
    test_windows_by_user: dict[str, list[FeatureWindow]],
) -> dict[str, CrossEvalResult]:
    """For every user's model, score that user's own held-out (genuine) test
    windows and every *other* user's held-out windows (zero-effort
    impostor). Every artifact must be for the same modality (callers
    typically call this once per modality).

    P3 note: this function only ever reads windows -- it never mutates or
    retrains a model with another user's data, so the cross-evaluation
    cannot become a contamination path.
    """
    results: dict[str, CrossEvalResult] = {}
    for user_id, artifact in artifacts_by_user.items():
        genuine_windows = test_windows_by_user.get(user_id, [])
        genuine_scores = _modality_scores(artifact, genuine_windows)

        impostor_scores: dict[str, list[float]] = {}
        for other_id, other_windows in test_windows_by_user.items():
            if other_id == user_id:
                continue
            scores = _modality_scores(artifact, other_windows)
            if scores:
                impostor_scores[other_id] = scores

        results[user_id] = CrossEvalResult(
            user_id=user_id, genuine_scores=genuine_scores, impostor_scores=impostor_scores
        )
    return results


def pairwise_far_matrix(
    results: dict[str, CrossEvalResult],
    *,
    threshold: float,
) -> dict[str, dict[str, float]]:
    """(genuine model owner) x (impostor identity) -> FAR at a fixed threshold.

    This is the "I1 cross-user matrix" required by the T-011 acceptance
    criteria: for each model owner, what fraction of *each other specific
    user's* windows would that model falsely accept.
    """
    matrix: dict[str, dict[str, float]] = {}
    for user_id, result in results.items():
        row: dict[str, float] = {}
        for other_id, scores in result.impostor_scores.items():
            arr = np.asarray(scores, dtype=float)
            row[other_id] = float(np.mean(arr >= threshold)) if len(arr) else float("nan")
        matrix[user_id] = row
    return matrix
