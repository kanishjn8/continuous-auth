"""Simple statistical baseline: Mahalanobis distance to the user's own
feature centroid (PLAN.md Section 10.4, required baseline #1).
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from ml.features.config import MLConfig
from ml.features.schema import FeatureWindow
from ml.training.common import ModelArtifact, Modality, train_one_class_model


class MahalanobisWrapper:
    """Higher ``normality_score`` = more normal (closer to the training centroid)."""

    def __init__(self, ridge: float = 1e-6):
        self.ridge = ridge
        self.mean_: np.ndarray | None = None
        self.inv_cov_: np.ndarray | None = None

    def fit(self, X: np.ndarray) -> "MahalanobisWrapper":
        self.mean_ = X.mean(axis=0)
        cov = np.atleast_2d(np.cov(X, rowvar=False))
        # Ridge regularization: per-user training sets can be small relative
        # to the feature dimension, which makes the empirical covariance
        # singular or ill-conditioned. Adding a small ridge term keeps the
        # inverse well-defined without materially changing the metric for
        # well-conditioned cases.
        cov_reg = cov + self.ridge * np.eye(cov.shape[0])
        self.inv_cov_ = np.linalg.pinv(cov_reg)
        return self

    def normality_score(self, X: np.ndarray) -> np.ndarray:
        assert self.mean_ is not None and self.inv_cov_ is not None
        diff = X - self.mean_
        dist_sq = np.einsum("ij,jk,ik->i", diff, self.inv_cov_, diff)
        distance = np.sqrt(np.clip(dist_sq, 0.0, None))
        return -distance


def train_user_modality_mahalanobis(
    user_id: str,
    modality: Modality,
    windows: Sequence[FeatureWindow],
    config: MLConfig,
) -> ModelArtifact:
    ridge = float(config.raw["mahalanobis_baseline"]["ridge"])
    hyperparameters = {"ridge": ridge}
    return train_one_class_model(
        user_id,
        modality,
        windows,
        model_factory=lambda: MahalanobisWrapper(ridge=ridge),
        model_type="mahalanobis_centroid",
        hyperparameters=hyperparameters,
        min_windows=config.raw["per_user_normalization"]["min_baseline_windows"],
    )
