"""Thin, sign-convention-normalising wrappers around sklearn one-class estimators.

Every wrapper exposes ``.fit(X)`` and ``.normality_score(X) -> np.ndarray``
where **higher = more normal**, matching
``sklearn.ensemble.IsolationForest.score_samples``. This lets
``ml/training/common.py``'s generic training/scoring routine and
``ml/calibration/percentile.py`` treat every model (the baseline Isolation
Forest and both required comparison models, PLAN.md Section 10.4)
identically.

The public training boundary calls ``require_promotion_gate`` before any
wrapper is constructed or fitted; these classes contain numerical estimator
mechanics only and never select training records themselves.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.svm import OneClassSVM


class IsolationForestWrapper:
    def __init__(self, **hyperparameters: Any):
        self._hp = hyperparameters
        self.model = IsolationForest(**hyperparameters)

    def fit(self, X: np.ndarray) -> IsolationForestWrapper:
        self.model.fit(X)
        return self

    def normality_score(self, X: np.ndarray) -> np.ndarray:
        return np.asarray(self.model.score_samples(X), dtype=float)


class OneClassSVMWrapper:
    """PLAN.md Section 10.4 required "alternative one-class model"."""

    def __init__(self, **hyperparameters: Any):
        self._hp = hyperparameters
        self.model = OneClassSVM(**hyperparameters)

    def fit(self, X: np.ndarray) -> OneClassSVMWrapper:
        self.model.fit(X)
        return self

    def normality_score(self, X: np.ndarray) -> np.ndarray:
        # OneClassSVM.decision_function: positive/high = inlier (normal),
        # already matching the shared sign convention.
        return np.asarray(self.model.decision_function(X), dtype=float)
