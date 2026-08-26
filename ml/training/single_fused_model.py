"""ADR-006 confirmation-criterion comparison baseline (PLAN.md Section 6,
ADR-006): a single Isolation Forest trained on the *concatenation* of the
keyboard and mouse feature blocks, instead of the production dual-model
score-fusion design.

"Phase 4 must run a direct comparison -- fused-vector single model vs
dual-model score fusion -- on the same day-disjoint split, and record the
result." This module exists ONLY to produce that comparison result. It is
NOT a competing production design and is not wired into the risk engine.

ADR-006's own rationale is that a single fused vector forces imputation on
any window missing one modality, and an Isolation Forest tends to read the
imputed values as anomalous rather than reading genuine unusual behavior.
``score_single_fused_window`` below reproduces that imputation step
faithfully -- zero-fill in raw feature space, the simplest thing a
single-vector design would have to do -- specifically so the comparison run
in ``ml/evaluation/pipeline.py`` surfaces the predicted failure mode instead
of hiding it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np

from ml.features.config import MLConfig
from ml.features.keyboard import KEYBOARD_FEATURE_NAMES
from ml.features.mouse import MOUSE_FEATURE_NAMES
from ml.features.schema import FeatureWindow
from ml.training.common import ModelArtifact, ModelSchemaMismatchError, train_one_class_model
from ml.training.model_wrappers import IsolationForestWrapper


def train_user_single_fused_model(
    user_id: str,
    windows: Sequence[FeatureWindow],
    config: MLConfig,
) -> ModelArtifact:
    """Train the ADR-006 single fused-vector comparison model.

    Only windows with *both* modalities present (``quality_label == FULL``)
    can supply a training row -- ``build_feature_matrix(..., "combined")``
    has no vector to concatenate otherwise, so those windows are silently
    excluded from training the same way ``InsufficientDataError`` already
    handles too few eligible windows.
    """
    hp = config.raw["isolation_forest"]
    hyperparameters = {
        "n_estimators": int(hp["n_estimators"]),
        "contamination": hp["contamination"],
        "max_samples": hp["max_samples"],
        "random_state": int(hp["random_state"]),
    }
    return train_one_class_model(
        user_id,
        "combined",
        windows,
        model_factory=lambda: IsolationForestWrapper(**hyperparameters),
        model_type="single_fused_isolation_forest",
        hyperparameters=hyperparameters,
        min_windows=config.raw["per_user_normalization"]["min_baseline_windows"],
    )


MissingModality = Literal["keyboard", "mouse"] | None


@dataclass
class SingleFusedScoreResult:
    available: bool
    raw_score: float | None
    percentile_score: float | None
    # Which modality had to be zero-imputed to build a full vector, or None
    # if both were present (or neither, in which case available=False).
    # This is the visible marker of the ADR-006-predicted failure mode.
    missing_modality: MissingModality


def score_single_fused_window(artifact: ModelArtifact, window: FeatureWindow) -> SingleFusedScoreResult:
    """Score one window against the single fused-vector artifact.

    Unlike ``ml.training.common.score_window`` (which reports a missing
    modality as simply unavailable -- the dual-model design's clean
    handling), a single fused-vector model has no way to score a partial
    window without first building a full-length vector. When exactly one
    modality is missing, this imputes it with zeros in raw feature space --
    the naive thing any single-vector design is forced to do -- and reports
    it via ``missing_modality`` so callers can measure how often that
    imputation makes an otherwise-genuine window look anomalous. When both
    modalities are missing there is nothing to impute from at all, so this
    refuses to fabricate a score (matches ADR-005: no score for
    ``INSUFFICIENT_DATA`` windows).
    """
    if window.feature_schema_version != artifact.feature_schema_version:
        raise ModelSchemaMismatchError(
            f"window feature_schema_version={window.feature_schema_version!r} != "
            f"artifact feature_schema_version={artifact.feature_schema_version!r}"
        )

    kbd, mouse = window.keyboard_features, window.mouse_features
    if kbd is None and mouse is None:
        return SingleFusedScoreResult(available=False, raw_score=None, percentile_score=None, missing_modality=None)

    missing: MissingModality = None
    if kbd is None:
        missing = "keyboard"
        kbd = {name: 0.0 for name in KEYBOARD_FEATURE_NAMES}
    if mouse is None:
        missing = "mouse"
        mouse = {name: 0.0 for name in MOUSE_FEATURE_NAMES}

    combined = {**kbd, **mouse}
    x = np.asarray([[combined[name] for name in artifact.feature_names]], dtype=float)
    x_scaled = artifact.preprocessing.transform(x)
    raw = float(artifact.model.normality_score(x_scaled)[0])
    if not np.isfinite(raw):
        raise ValueError(
            f"non-finite score for user={artifact.user_id!r} modality={artifact.modality!r} "
            f"window_id={window.window_id!r}"
        )
    pct = float(artifact.calibration.transform(np.asarray([raw]))[0])
    return SingleFusedScoreResult(available=True, raw_score=raw, percentile_score=pct, missing_modality=missing)
