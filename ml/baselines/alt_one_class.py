"""Required alternative one-class model: One-Class SVM (PLAN.md Section 10.4,
required baseline #2).
"""

from __future__ import annotations

from typing import Sequence

from ml.features.config import MLConfig
from ml.features.schema import FeatureWindow
from ml.training.common import ModelArtifact, Modality, train_one_class_model
from ml.training.model_wrappers import OneClassSVMWrapper


def train_user_modality_one_class_svm(
    user_id: str,
    modality: Modality,
    windows: Sequence[FeatureWindow],
    config: MLConfig,
) -> ModelArtifact:
    hp = config.raw["one_class_svm_baseline"]
    hyperparameters = {
        "kernel": hp["kernel"],
        "nu": float(hp["nu"]),
        "gamma": hp["gamma"],
    }
    return train_one_class_model(
        user_id,
        modality,
        windows,
        model_factory=lambda: OneClassSVMWrapper(**hyperparameters),
        model_type="one_class_svm",
        hyperparameters=hyperparameters,
        min_windows=config.raw["per_user_normalization"]["min_baseline_windows"],
    )
