"""T-011 orchestration: ties splitting + training + baselines + metrics +
cross-evaluation into the required comparisons, sharing identical
features/splits/calibration across every model compared (PLAN.md Section
10.4 requirement) and recording configuration/data/code versions
(Section 13.5, "Statistical Honesty Requirements").
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from ml.baselines.alt_one_class import train_user_modality_one_class_svm
from ml.baselines.mahalanobis import train_user_modality_mahalanobis
from ml.evaluation.cross_evaluation import CrossEvalResult, zero_effort_cross_evaluation
from ml.evaluation.fusion import fuse_percentile_scores, fused_cross_evaluation
from ml.evaluation.metrics import EerResult, PerUserMetrics, compute_eer, per_user_metric_spread
from ml.evaluation.splitting import DaySplit, day_disjoint_split, enrollment_length_subsets
from ml.features.config import MLConfig
from ml.features.schema import FeatureWindow, QualityLabel
from ml.training.common import InsufficientDataError, Modality, ModelArtifact, score_window
from ml.training.enrollment import EnrollmentAdmission
from ml.training.isolation_forest import train_user_modality_isolation_forest
from ml.training.single_fused_model import score_single_fused_window, train_user_single_fused_model

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
    admission: EnrollmentAdmission | None = None,
) -> dict[str, ModelComparisonResult]:
    """Run the required baseline comparison (PLAN.md Section 10.4): identical
    day-disjoint splits, identical feature set, identical calibration
    procedure, for every model type in ``model_types``.

    ``admission`` admits a first profile's participant data per ADR-013;
    leave it ``None`` for synthetic, public, or already-promoted data.
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
            try:
                split: DaySplit = day_disjoint_split(windows, test_days=test_days)
            except ValueError as e:
                excluded[user_id] = str(e)
                continue
            try:
                artifact = trainer(user_id, modality, split.train, config, enrollment_admission=admission)
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


@dataclass
class MissingModalityHandling:
    """PLAN.md ADR-005/ADR-006: how a scoring approach handles a genuine
    user's own test window where one modality's quality gate was not met.

    Reported separately per approach (dual-model fusion vs the single
    fused-vector model) and per which modality was missing, so the ADR-006
    comparison surfaces *behavior* on these windows rather than folding
    them into one headline EER number.
    """

    n_windows: int
    n_scored: int
    mean_percentile_score: float | None
    fraction_below_threshold: float | None
    anomaly_threshold: float


def _summarize_missing_modality(
    windows: list[FeatureWindow], scores: list[float], anomaly_threshold: float
) -> MissingModalityHandling:
    if not scores:
        return MissingModalityHandling(
            n_windows=len(windows),
            n_scored=0,
            mean_percentile_score=None,
            fraction_below_threshold=None,
            anomaly_threshold=anomaly_threshold,
        )
    arr = np.asarray(scores, dtype=float)
    return MissingModalityHandling(
        n_windows=len(windows),
        n_scored=len(scores),
        mean_percentile_score=float(np.mean(arr)),
        fraction_below_threshold=float(np.mean(arr < anomaly_threshold)),
        anomaly_threshold=anomaly_threshold,
    )


def _missing_modality_handling_dual(
    kbd_artifacts: dict[str, ModelArtifact],
    mouse_artifacts: dict[str, ModelArtifact],
    test_windows_by_user: dict[str, list[FeatureWindow]],
    quality_label: QualityLabel,
    *,
    w_kbd: float,
    w_mouse: float,
    anomaly_threshold: float,
) -> MissingModalityHandling:
    windows: list[FeatureWindow] = []
    scores: list[float] = []
    for user_id, test_windows in test_windows_by_user.items():
        kbd_artifact = kbd_artifacts.get(user_id)
        mouse_artifact = mouse_artifacts.get(user_id)
        for w in test_windows:
            if w.quality_label != quality_label:
                continue
            windows.append(w)
            kbd_r = score_window(kbd_artifact, w) if kbd_artifact is not None else None
            mouse_r = score_window(mouse_artifact, w) if mouse_artifact is not None else None
            fused = fuse_percentile_scores(
                kbd_r.percentile_score if kbd_r is not None and kbd_r.available else None,
                mouse_r.percentile_score if mouse_r is not None and mouse_r.available else None,
                w_kbd=w_kbd,
                w_mouse=w_mouse,
            )
            if fused is not None:
                scores.append(fused)
    return _summarize_missing_modality(windows, scores, anomaly_threshold)


def _missing_modality_handling_single(
    single_artifacts: dict[str, ModelArtifact],
    test_windows_by_user: dict[str, list[FeatureWindow]],
    quality_label: QualityLabel,
    *,
    anomaly_threshold: float,
) -> MissingModalityHandling:
    windows: list[FeatureWindow] = []
    scores: list[float] = []
    for user_id, test_windows in test_windows_by_user.items():
        artifact = single_artifacts.get(user_id)
        if artifact is None:
            continue
        for w in test_windows:
            if w.quality_label != quality_label:
                continue
            windows.append(w)
            r = score_single_fused_window(artifact, w)
            if r.available and r.percentile_score is not None:
                scores.append(r.percentile_score)
    return _summarize_missing_modality(windows, scores, anomaly_threshold)


def _eer_and_counts_from_cross(
    cross: dict[str, CrossEvalResult],
) -> tuple[dict[str, float], dict[str, int], dict[str, int]]:
    per_user_eer: dict[str, float] = {}
    n_genuine: dict[str, int] = {}
    n_impostor: dict[str, int] = {}
    for user_id, r in cross.items():
        impostor = r.all_impostor_scores()
        if len(r.genuine_scores) == 0 or len(impostor) == 0:
            continue
        eer_result = compute_eer(np.asarray(r.genuine_scores), impostor)
        per_user_eer[user_id] = eer_result.eer
        n_genuine[user_id] = len(r.genuine_scores)
        n_impostor[user_id] = len(impostor)
    return per_user_eer, n_genuine, n_impostor


def _aggregate_eer(
    per_user_eer: dict[str, float], n_genuine: dict[str, int], n_impostor: dict[str, int]
) -> dict[str, float]:
    if not per_user_eer:
        return {}
    metrics_list = [
        PerUserMetrics(
            user_id=u, n_genuine=n_genuine[u], n_impostor=n_impostor[u], eer=eer, eer_threshold=0.0
        )
        for u, eer in per_user_eer.items()
    ]
    return per_user_metric_spread(metrics_list)


def _single_fused_cross_evaluation(
    single_artifacts: dict[str, ModelArtifact],
    test_windows_by_user: dict[str, list[FeatureWindow]],
) -> dict[str, CrossEvalResult]:
    """Mirrors ``zero_effort_cross_evaluation`` but scores through the
    ADR-006 single fused-vector model, imputing a missing modality rather
    than treating it as unavailable (see ``score_single_fused_window``).
    """
    results: dict[str, CrossEvalResult] = {}
    for user_id, artifact in single_artifacts.items():
        genuine_windows = test_windows_by_user.get(user_id, [])
        genuine_scores = []
        for window in genuine_windows:
            result = score_single_fused_window(artifact, window)
            if result.available and result.percentile_score is not None:
                genuine_scores.append(result.percentile_score)

        impostor_scores: dict[str, list[float]] = {}
        for other_id, other_windows in test_windows_by_user.items():
            if other_id == user_id:
                continue
            scores = []
            for window in other_windows:
                result = score_single_fused_window(artifact, window)
                if result.available and result.percentile_score is not None:
                    scores.append(result.percentile_score)
            if scores:
                impostor_scores[other_id] = scores

        results[user_id] = CrossEvalResult(
            user_id=user_id, genuine_scores=genuine_scores, impostor_scores=impostor_scores
        )
    return results


@dataclass
class ComparisonArm:
    """One arm of the ADR-006 confirmation comparison, evaluated on the
    identical day-disjoint split and (for the fusion arms) identical
    trained per-modality artifacts.
    """

    label: str
    per_user_eer: dict[str, float] = field(default_factory=dict)
    per_user_n_genuine: dict[str, int] = field(default_factory=dict)
    per_user_n_impostor: dict[str, int] = field(default_factory=dict)
    aggregate: dict[str, float] = field(default_factory=dict)
    # Keyed "kbd_only" / "mouse_only" -- how this arm handled a genuine
    # user's own test windows where the other modality's quality gate
    # (ADR-005) was not met.
    missing_modality: dict[str, MissingModalityHandling] = field(default_factory=dict)


@dataclass
class FusionVsSingleModelResult:
    """PLAN.md ADR-006 confirmation criterion: "Phase 4 must run a direct
    comparison -- fused-vector single model vs dual-model score fusion --
    on the same day-disjoint split, and record the result."

    ``dual_model_fusion`` is the production design (ADR-006): two
    per-modality Isolation Forest models, combined via
    ``ml.evaluation.fusion.fused_cross_evaluation``. ``single_fused_model``
    is the ADR-006 comparison baseline built for this confirmation only
    (``ml.training.single_fused_model``). ``keyboard_only``/``mouse_only``
    are included for the same fusion-ablation context
    ``ml.evaluation.fusion`` already documents (keyboard-only vs mouse-only
    vs fused).
    """

    dual_model_fusion: ComparisonArm
    single_fused_model: ComparisonArm
    keyboard_only: ComparisonArm
    mouse_only: ComparisonArm
    excluded_users: dict[str, str] = field(default_factory=dict)
    single_model_excluded_users: dict[str, str] = field(default_factory=dict)
    kbd_artifacts: dict[str, ModelArtifact] = field(default_factory=dict)
    mouse_artifacts: dict[str, ModelArtifact] = field(default_factory=dict)
    single_artifacts: dict[str, ModelArtifact] = field(default_factory=dict)


def run_fusion_vs_single_model_comparison(
    corpus: dict[str, list[FeatureWindow]],
    test_days_by_user: dict[str, Sequence[str]],
    config: MLConfig,
    *,
    w_kbd: float = 0.5,
    w_mouse: float = 0.5,
    anomaly_threshold: float = 50.0,
    admission: EnrollmentAdmission | None = None,
) -> FusionVsSingleModelResult:
    """Run the ADR-006 confirmation-criterion comparison against real
    trained artifacts: dual-model score fusion (``ml.evaluation.fusion``)
    vs the single fused-vector model (``ml.training.single_fused_model``),
    on the same day-disjoint split, per user.

    ``admission`` admits a first profile's participant data per ADR-013;
    leave it ``None`` for synthetic, public, or already-promoted data.
    """
    kbd_artifacts: dict[str, ModelArtifact] = {}
    mouse_artifacts: dict[str, ModelArtifact] = {}
    single_artifacts: dict[str, ModelArtifact] = {}
    test_windows_by_user: dict[str, list[FeatureWindow]] = {}
    excluded: dict[str, str] = {}
    single_excluded: dict[str, str] = {}

    for user_id, windows in corpus.items():
        test_days = test_days_by_user.get(user_id)
        if not test_days:
            excluded[user_id] = "no test_days configured for this user"
            continue
        try:
            split: DaySplit = day_disjoint_split(windows, test_days=test_days)
        except ValueError as e:
            excluded[user_id] = str(e)
            continue

        try:
            kbd_artifacts[user_id] = train_user_modality_isolation_forest(
                user_id, "keyboard", split.train, config, enrollment_admission=admission
            )
            mouse_artifacts[user_id] = train_user_modality_isolation_forest(
                user_id, "mouse", split.train, config, enrollment_admission=admission
            )
        except InsufficientDataError as e:
            excluded[user_id] = str(e)
            continue

        test_windows_by_user[user_id] = split.test

        try:
            single_artifacts[user_id] = train_user_single_fused_model(
                user_id, split.train, config, enrollment_admission=admission
            )
        except InsufficientDataError as e:
            # The single fused-vector model trains only on FULL windows
            # (both modalities present) -- it can independently run short
            # of data even when the dual-model arm above succeeded. That
            # gap is itself part of the ADR-006 comparison, not an error.
            single_excluded[user_id] = str(e)

    dual_cross = fused_cross_evaluation(
        kbd_artifacts, mouse_artifacts, test_windows_by_user, w_kbd=w_kbd, w_mouse=w_mouse
    )
    kbd_cross = zero_effort_cross_evaluation(kbd_artifacts, test_windows_by_user)
    mouse_cross = zero_effort_cross_evaluation(mouse_artifacts, test_windows_by_user)
    single_cross = _single_fused_cross_evaluation(single_artifacts, test_windows_by_user)

    dual_eer, dual_ng, dual_ni = _eer_and_counts_from_cross(dual_cross)
    kbd_eer, kbd_ng, kbd_ni = _eer_and_counts_from_cross(kbd_cross)
    mouse_eer, mouse_ng, mouse_ni = _eer_and_counts_from_cross(mouse_cross)
    single_eer, single_ng, single_ni = _eer_and_counts_from_cross(single_cross)

    dual_arm = ComparisonArm(
        label="dual_model_score_fusion",
        per_user_eer=dual_eer,
        per_user_n_genuine=dual_ng,
        per_user_n_impostor=dual_ni,
        aggregate=_aggregate_eer(dual_eer, dual_ng, dual_ni),
        missing_modality={
            "kbd_only": _missing_modality_handling_dual(
                kbd_artifacts,
                mouse_artifacts,
                test_windows_by_user,
                QualityLabel.KBD_ONLY,
                w_kbd=w_kbd,
                w_mouse=w_mouse,
                anomaly_threshold=anomaly_threshold,
            ),
            "mouse_only": _missing_modality_handling_dual(
                kbd_artifacts,
                mouse_artifacts,
                test_windows_by_user,
                QualityLabel.MOUSE_ONLY,
                w_kbd=w_kbd,
                w_mouse=w_mouse,
                anomaly_threshold=anomaly_threshold,
            ),
        },
    )
    single_arm = ComparisonArm(
        label="single_fused_vector_model",
        per_user_eer=single_eer,
        per_user_n_genuine=single_ng,
        per_user_n_impostor=single_ni,
        aggregate=_aggregate_eer(single_eer, single_ng, single_ni),
        missing_modality={
            "kbd_only": _missing_modality_handling_single(
                single_artifacts,
                test_windows_by_user,
                QualityLabel.KBD_ONLY,
                anomaly_threshold=anomaly_threshold,
            ),
            "mouse_only": _missing_modality_handling_single(
                single_artifacts,
                test_windows_by_user,
                QualityLabel.MOUSE_ONLY,
                anomaly_threshold=anomaly_threshold,
            ),
        },
    )
    kbd_arm = ComparisonArm(
        label="keyboard_only",
        per_user_eer=kbd_eer,
        per_user_n_genuine=kbd_ng,
        per_user_n_impostor=kbd_ni,
        aggregate=_aggregate_eer(kbd_eer, kbd_ng, kbd_ni),
    )
    mouse_arm = ComparisonArm(
        label="mouse_only",
        per_user_eer=mouse_eer,
        per_user_n_genuine=mouse_ng,
        per_user_n_impostor=mouse_ni,
        aggregate=_aggregate_eer(mouse_eer, mouse_ng, mouse_ni),
    )

    return FusionVsSingleModelResult(
        dual_model_fusion=dual_arm,
        single_fused_model=single_arm,
        keyboard_only=kbd_arm,
        mouse_only=mouse_arm,
        excluded_users=excluded,
        single_model_excluded_users=single_excluded,
        kbd_artifacts=kbd_artifacts,
        mouse_artifacts=mouse_artifacts,
        single_artifacts=single_artifacts,
    )


def run_enrollment_length_experiment(
    user_id: str,
    modality: Modality,
    windows: list[FeatureWindow],
    *,
    day_lengths: Sequence[int],
    holdout_days: Sequence[str],
    config: MLConfig,
    impostor_windows: list[FeatureWindow] | None = None,
    admission: EnrollmentAdmission | None = None,
) -> dict[int, dict[str, object]]:
    """PLAN.md Section 10.5: train on 1/2/3/5/7+ days, evaluate against a
    held-out partition held constant across all lengths, report FAR/FRR/EER
    (when impostor windows are supplied) or a genuine-only FRR diagnostic
    otherwise, per enrollment length.

    ``admission`` admits a first profile's participant data per ADR-013;
    leave it ``None`` for synthetic, public, or already-promoted data.
    """
    subsets = enrollment_length_subsets(windows, day_lengths=day_lengths, holdout_days=holdout_days)
    holdout_set = set(holdout_days)
    holdout_windows = [w for w in windows if w.collection_day in holdout_set]
    if not holdout_windows:
        raise ValueError("holdout_days matched no windows -- cannot evaluate any enrollment length")

    results: dict[int, dict[str, object]] = {}
    for n_days, train_windows in subsets.items():
        try:
            artifact = train_user_modality_isolation_forest(
                user_id, modality, train_windows, config, enrollment_admission=admission
            )
        except InsufficientDataError as e:
            results[n_days] = {"status": "insufficient_data", "reason": str(e)}
            continue

        genuine_scores = []
        for window in holdout_windows:
            result = score_window(artifact, window)
            if result.available and result.percentile_score is not None:
                genuine_scores.append(result.percentile_score)
        entry: dict[str, object] = {
            "status": "ok",
            "n_training_windows": len(train_windows),
            "n_genuine_test": len(genuine_scores),
        }
        if impostor_windows:
            impostor_scores = []
            for window in impostor_windows:
                result = score_window(artifact, window)
                if result.available and result.percentile_score is not None:
                    impostor_scores.append(result.percentile_score)
            entry["n_impostor_test"] = len(impostor_scores)
            if genuine_scores and impostor_scores:
                eer: EerResult = compute_eer(
                    np.asarray(genuine_scores), np.asarray(impostor_scores)
                )
                entry["eer"] = eer.eer
                entry["eer_threshold"] = eer.threshold
        elif genuine_scores:
            # No impostor data supplied: report a genuine-only diagnostic
            # (fraction of genuine windows below the distribution's own
            # median) rather than fabricating an EER without impostor data.
            entry["frr_at_median_threshold"] = float(np.mean(np.asarray(genuine_scores) < 50.0))
        results[n_days] = entry
    return results
