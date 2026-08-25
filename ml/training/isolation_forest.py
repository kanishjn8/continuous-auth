"""T-010 primary entry point: per-user, per-modality Isolation Forest training.

PLAN.md Section 10.1: "Baseline model: Isolation Forest (scikit-learn), one
per modality per user (ADR-006)." Hyperparameters come from
``ml/config/thresholds.yaml`` (guardrail: no hardcoded threshold/weight
outside config).
"""

from __future__ import annotations

from typing import Sequence

from ml.features.config import MLConfig
from ml.features.schema import FeatureWindow
from ml.training.common import ModelArtifact, Modality, train_one_class_model
from ml.training.model_wrappers import IsolationForestWrapper


def train_user_modality_isolation_forest(
    user_id: str,
    modality: Modality,
    windows: Sequence[FeatureWindow],
    config: MLConfig,
) -> ModelArtifact:
    hp = config.raw["isolation_forest"]
    hyperparameters = {
        "n_estimators": int(hp["n_estimators"]),
        "contamination": hp["contamination"],
        "max_samples": hp["max_samples"],
        "random_state": int(hp["random_state"]),
    }
    return train_one_class_model(
        user_id,
        modality,
        windows,
        model_factory=lambda: IsolationForestWrapper(**hyperparameters),
        model_type="isolation_forest",
        hyperparameters=hyperparameters,
        min_windows=config.raw["per_user_normalization"]["min_baseline_windows"],
    )


def train_user_profile(
    user_id: str,
    windows: Sequence[FeatureWindow],
    config: MLConfig,
) -> dict[Modality, ModelArtifact]:
    """Train both modality models for one user, skipping a modality that has
    too little data rather than failing the whole profile (PLAN.md ADR-006:
    the two models are independent; one being unavailable never blocks the
    other, mirroring the availability-fusion design of the risk engine).
    """
    from ml.training.common import InsufficientDataError

    profile: dict[Modality, ModelArtifact] = {}
    for modality in ("keyboard", "mouse"):
        try:
            profile[modality] = train_user_modality_isolation_forest(user_id, modality, windows, config)
        except InsufficientDataError:
            continue
    return profile
