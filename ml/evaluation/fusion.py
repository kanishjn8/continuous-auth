"""Modality-fusion ablation support (PLAN.md Section 10.7, ADR-006 confirmation criterion).

"Phase 4 must run a direct comparison -- fused-vector single model vs
dual-model score fusion -- on the same day-disjoint split, and record the
result." This module provides the **evaluation-time** availability-weighted
fusion needed to report that ablation (keyboard-only vs mouse-only vs
fused). It is intentionally separate from, and simpler than, the live risk
engine's fusion (T-013, Joel) -- EWMA smoothing, hysteresis, and context
confidence are risk-engine concerns, not an ML evaluation concern.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ml.evaluation.cross_evaluation import CrossEvalResult
from ml.features.schema import FeatureWindow
from ml.training.common import ModelArtifact, score_window


def fuse_percentile_scores(
    kbd_pct: float | None,
    mouse_pct: float | None,
    *,
    w_kbd: float = 0.5,
    w_mouse: float = 0.5,
) -> float | None:
    """Availability-renormalised fusion of two calibrated percentile scores.

    Returns ``None`` only if *both* modalities are unavailable (ADR-005:
    that case must not produce a score at all).
    """
    if kbd_pct is None and mouse_pct is None:
        return None
    if kbd_pct is None:
        return mouse_pct
    if mouse_pct is None:
        return kbd_pct
    total_weight = w_kbd + w_mouse
    if total_weight <= 0:
        raise ValueError("w_kbd + w_mouse must be > 0")
    return (w_kbd * kbd_pct + w_mouse * mouse_pct) / total_weight


def fused_cross_evaluation(
    kbd_artifacts_by_user: dict[str, ModelArtifact],
    mouse_artifacts_by_user: dict[str, ModelArtifact],
    test_windows_by_user: dict[str, list[FeatureWindow]],
    *,
    w_kbd: float = 0.5,
    w_mouse: float = 0.5,
) -> dict[str, CrossEvalResult]:
    """Zero-effort cross-evaluation using availability-weighted fused scores.

    A user must have both a keyboard and a mouse artifact to be included as
    a model owner (fusion ablation compares against dual-model fusion, per
    ADR-006 -- a user with only one modality trained is reported separately
    by the single-modality cross-evaluation, not silently included here
    with one weight at zero).
    """
    users = sorted(set(kbd_artifacts_by_user) & set(mouse_artifacts_by_user))
    results: dict[str, CrossEvalResult] = {}

    for user_id in users:
        kbd_artifact = kbd_artifacts_by_user[user_id]
        mouse_artifact = mouse_artifacts_by_user[user_id]

        genuine_scores = _fused_scores(kbd_artifact, mouse_artifact, test_windows_by_user.get(user_id, []), w_kbd, w_mouse)

        impostor_scores: dict[str, list[float]] = {}
        for other_id, other_windows in test_windows_by_user.items():
            if other_id == user_id:
                continue
            scores = _fused_scores(kbd_artifact, mouse_artifact, other_windows, w_kbd, w_mouse)
            if scores:
                impostor_scores[other_id] = scores

        results[user_id] = CrossEvalResult(user_id=user_id, genuine_scores=genuine_scores, impostor_scores=impostor_scores)

    return results


def _fused_scores(
    kbd_artifact: ModelArtifact,
    mouse_artifact: ModelArtifact,
    windows: list[FeatureWindow],
    w_kbd: float,
    w_mouse: float,
) -> list[float]:
    scores: list[float] = []
    for w in windows:
        kbd_result = score_window(kbd_artifact, w)
        mouse_result = score_window(mouse_artifact, w)
        fused = fuse_percentile_scores(
            kbd_result.percentile_score if kbd_result.available else None,
            mouse_result.percentile_score if mouse_result.available else None,
            w_kbd=w_kbd,
            w_mouse=w_mouse,
        )
        if fused is not None:
            scores.append(fused)
    return scores
