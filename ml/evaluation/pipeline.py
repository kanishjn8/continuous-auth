"""T-011 orchestration: ties splitting + training + baselines + metrics +
cross-evaluation into the required comparisons, sharing identical
features/splits/calibration across every model compared (PLAN.md Section
10.4 requirement) and recording configuration/data/code versions
(Section 13.5, "Statistical Honesty Requirements").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from ml.baselines.alt_one_class import train_user_modality_one_class_svm
from ml.baselines.mahalanobis import train_user_modality_mahalanobis
from ml.evaluation.cross_evaluation import CrossEvalResult, zero_effort_cross_evaluation
from ml.evaluation.metrics import EerResult, PerUserMetrics, compute_eer, per_user_metric_spread
from ml.evaluation.splitting import DaySplit, day_disjoint_split, enrollment_length_subsets
from ml.features.config import MLConfig
from ml.features.schema import FeatureWindow
from ml.training.common import InsufficientDataError, ModelArtifact, Modality, score_window
from ml.training.isolation_forest import train_user_modality_isolation_forest

_TRAINERS = {
    "isolation_forest": train_user_modality_isolation_forest,
    "mahalanobis_centroid": train_user_modality_mahalanobis,
    "one_class_svm": train_user_modality_one_class_svm,
}


@dataclass
class ModelComparisonResult:
    model_type: str
    modality: Modality
    per_user_eer: dict[str, float] = field(default_factory=dict)
    per_user_n_genuine: dict[str, int] = field(default_factory=dict)
    per_user_n_impostor: dict[str, int] = field(default_factory=dict)
    excluded_users: dict[str, str] = field(default_factory=dict)  # user_id -> reason
    aggregate: dict[str, float] = field(default_factory=dict)
    # Trained artifacts, keyed by user_id -- PLAN.md Section 13.5 requires
    # every reported result to trace back to its exact config/data/code
    # version; each ModelArtifact carries hyperparameters, checksum,
    # feature-schema version, and the training day range, so keeping them
    # here (rather than discarding after computing EER) is what makes a
    # reported number reproducible/traceable rather than just a float.
    artifacts: dict[str, ModelArtifact] = field(default_factory=dict)


def run_baseline_comparison(
    corpus: dict[str, list[FeatureWindow]],
    modality: Modality,
    test_days_by_user: dict[str, Sequence[str]],
    config: MLConfig,
    *,
    model_types: Sequence[str] = ("isolation_forest", "mahalanobis_centroid", "one_class_svm"),
) -> dict[str, ModelComparisonResult]:
    """Run the required baseline comparison (PLAN.md Section 10.4): identical
    day-disjoint splits, identical feature set, identical calibration
    procedure, for every model type in ``model_types``.
    """
    results: dict[str, ModelComparisonResult] = {}

    for model_type in model_types:
        trainer = _TRAINERS[model_type]
        artifacts: dict[str, ModelArtifact] = {}
        test_windows_by_user: dict[str, list[FeatureWindow]] = {}
        excluded: dict[str, str] = {}

        for user_id, windows in corpus.items():
            test_days = test_days_by_user.get(user_id)
            if not test_days:
                excluded[user_id] = "no test_days configured for this user"
                continue
            split: DaySplit = day_disjoint_split(windows, test_days=test_days)
            try:
                artifact = trainer(user_id, modality, split.train, config)
            except InsufficientDataError as e:
                excluded[user_id] = str(e)
                continue
            artifacts[user_id] = artifact
            test_windows_by_user[user_id] = split.test

        cross = zero_effort_cross_evaluation(artifacts, test_windows_by_user)

        per_user_eer: dict[str, float] = {}
        per_user_n_genuine: dict[str, int] = {}
        per_user_n_impostor: dict[str, int] = {}
        for user_id, r in cross.items():
            impostor = r.all_impostor_scores()
            if len(r.genuine_scores) == 0 or len(impostor) == 0:
                excluded[user_id] = "no genuine or no impostor scores available for EER"
                continue
            eer_result = compute_eer(np.asarray(r.genuine_scores), impostor)
            per_user_eer[user_id] = eer_result.eer
            per_user_n_genuine[user_id] = len(r.genuine_scores)
            per_user_n_impostor[user_id] = len(impostor)

        aggregate: dict[str, float] = {}
        if per_user_eer:
            metrics_list = [
                PerUserMetrics(
                    user_id=u,
                    n_genuine=per_user_n_genuine[u],
                    n_impostor=per_user_n_impostor[u],
                    eer=eer,
                    eer_threshold=0.0,
                )
                for u, eer in per_user_eer.items()
            ]
            aggregate = per_user_metric_spread(metrics_list)

        results[model_type] = ModelComparisonResult(
            model_type=model_type,
            modality=modality,
            per_user_eer=per_user_eer,
            per_user_n_genuine=per_user_n_genuine,
            per_user_n_impostor=per_user_n_impostor,
            excluded_users=excluded,
            artifacts=artifacts,
            aggregate=aggregate,
        )

    return results


def run_enrollment_length_experiment(
    user_id: str,
    modality: Modality,
    windows: list[FeatureWindow],
    *,
    day_lengths: Sequence[int],
    holdout_days: Sequence[str],
    config: MLConfig,
    impostor_windows: list[FeatureWindow] | None = None,
) -> dict[int, dict]:
    """PLAN.md Section 10.5: train on 1/2/3/5/7+ days, evaluate against a
    held-out partition held constant across all lengths, report FAR/FRR/EER
    (when impostor windows are supplied) or a genuine-only FRR diagnostic
    otherwise, per enrollment length.
    """
    subsets = enrollment_length_subsets(windows, day_lengths=day_lengths, holdout_days=holdout_days)
    holdout_set = set(holdout_days)
    holdout_windows = [w for w in windows if w.collection_day in holdout_set]
    if not holdout_windows:
        raise ValueError("holdout_days matched no windows -- cannot evaluate any enrollment length")

    results: dict[int, dict] = {}
    for n_days, train_windows in subsets.items():
        try:
            artifact = train_user_modality_isolation_forest(user_id, modality, train_windows, config)
        except InsufficientDataError as e:
            results[n_days] = {"status": "insufficient_data", "reason": str(e)}
            continue

        genuine_scores = [
            r.percentile_score
            for w in holdout_windows
            if (r := score_window(artifact, w)).available
        ]
        entry: dict = {
            "status": "ok",
            "n_training_windows": len(train_windows),
            "n_genuine_test": len(genuine_scores),
        }
        if impostor_windows:
            impostor_scores = [
                r.percentile_score
                for w in impostor_windows
                if (r := score_window(artifact, w)).available
            ]
            entry["n_impostor_test"] = len(impostor_scores)
            if genuine_scores and impostor_scores:
                eer: EerResult = compute_eer(np.asarray(genuine_scores), np.asarray(impostor_scores))
                entry["eer"] = eer.eer
                entry["eer_threshold"] = eer.threshold
        elif genuine_scores:
            # No impostor data supplied: report a genuine-only diagnostic
            # (fraction of genuine windows below the distribution's own
            # median) rather than fabricating an EER without impostor data.
            entry["frr_at_median_threshold"] = float(np.mean(np.asarray(genuine_scores) < 50.0))
        results[n_days] = entry
    return results
