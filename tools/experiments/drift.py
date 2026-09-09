"""E1 -- drift-benefit experiment driver (PLAN.md Section 12.4; design doc Section 8.6).

Replays the pilot corpus, ordered by date, through the real production
components: ``EnrollmentAdmission`` trains a "frozen" profile on an early
subset of TRAIN-partition days, and ``tools.updates.run.run_update`` (the
Model Update Manager's own scheduled-retraining path) trains an "updated"
profile from the full TRAIN partition once quarantine has elapsed. Both
profiles are then scored, unmodified, against the same held-out EVALUATION
windows so that PLAN.md Section 12.4's question -- does FRR drift without
updates, and does updating recover it without degrading FAR -- has a
genuine, paired answer.

RESULTS FROM THIS MODULE ARE NOT EVIDENCE of anything about the pilot cohort
until it has been run against the frozen ELIGIBLE corpus with both freezes
verified: the collection freeze (``tools.collection.corpus.load_frozen_corpus``,
checked on every partition load) and the evaluation freeze
(``tools.evaluation.freeze.verify_evaluation_freeze``, checked here before
any evaluation record is read). This matches ``docs/update-manager.md``. A
run against the synthetic fixtures in ``tools/experiments/tests/`` exercises
the mechanics only and proves nothing about real participants.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import yaml

from backend.app.decisions import VerificationRecord
from backend.app.risk.config import RiskSettings
from backend.app.runtime.profiles import DirectoryProfileProvider
from backend.app.storage.service import StorageService
from backend.app.updates.config import UpdateSettings, load_update_settings
from backend.app.updates.manager import (
    ModelProfile,
    SegmentEvidence,
    UpdateManager,
    ValidationMetrics,
    ValidationReport,
)
from backend.app.updates.repository import SQLiteUpdateRepository
from ml.evaluation.cross_evaluation import zero_effort_cross_evaluation
from ml.evaluation.metrics import compute_eer, compute_far_frr
from ml.experiments.update_manager import DriftBenefitResult, evaluate_drift_benefit
from ml.features.config import MLConfig
from ml.features.schema import FeatureWindow
from ml.training.enrollment import EnrollmentAdmission
from ml.training.isolation_forest import train_user_profile
from ml.training.persistence import save_artifact
from protocol.generated.python.contracts import RiskLevel, VerificationAnchor
from tools.collection import corpus as corpus_module
from tools.collection.config import CollectionSettings
from tools.collection.repository import load_administration_records
from tools.evaluation.freeze import verify_evaluation_freeze
from tools.updates.run import run_update

_SEGMENT_COMPLETED_AT = datetime(2026, 9, 5, tzinfo=UTC)
_MIN_RISK = 1e-6


def _has_active_profile(storage: StorageService, user_id: str) -> bool:
    with storage.database.connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM model_profiles WHERE user_id = ? AND status = 'ACTIVE'",
            (user_id,),
        ).fetchone()
    return row is not None


def _aggregate_checksum(profile_version: str, keyboard_artifact, mouse_artifact) -> str:
    import hashlib

    values = (
        profile_version,
        keyboard_artifact.version if keyboard_artifact else "",
        keyboard_artifact.checksum if keyboard_artifact else "",
        mouse_artifact.version if mouse_artifact else "",
        mouse_artifact.checksum if mouse_artifact else "",
    )
    return hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()


def _pooled_percentile_scores(
    user_id: str,
    profile: dict[str, object],
    genuine_windows: list[FeatureWindow],
    impostor_windows_by_user: dict[str, list[FeatureWindow]],
) -> tuple[np.ndarray, np.ndarray]:
    """Pool percentile scores across every trained modality of ``profile``.

    Percentile scores share one calibrated 0-100 scale by construction
    (``ml/calibration/percentile.py``), so pooling across modalities before
    reporting one genuine/impostor pair is the same convention already used
    by ``tools/enrollment/activate.py`` and ``tools/updates/run.py``.
    """

    windows_by_user = {**impostor_windows_by_user, user_id: genuine_windows}
    genuine: list[float] = []
    impostor: list[float] = []
    for artifact in profile.values():
        cross = zero_effort_cross_evaluation({user_id: artifact}, windows_by_user)
        result = cross.get(user_id)
        if result is None:
            continue
        genuine.extend(result.genuine_scores)
        impostor.extend(result.all_impostor_scores().tolist())
    return np.asarray(genuine, dtype=float), np.asarray(impostor, dtype=float)


def _measure_frozen_profile_metrics(
    user_id: str,
    frozen_profile: dict[str, object],
    validation_windows_by_user: dict[str, list[FeatureWindow]],
) -> ValidationMetrics:
    """Measure the frozen profile's FRR/FAR on VALIDATION -- never fabricated.

    Mirrors ``tools.enrollment.activate._measure_validation_metrics`` exactly:
    FRR is the fraction of the participant's own held-out VALIDATION windows
    the frozen profile rejects; FAR is the fraction of every other
    participant's VALIDATION windows the same profile falsely accepts (via
    ``zero_effort_cross_evaluation``, pooled across modalities the same way
    ``_pooled_percentile_scores`` is used elsewhere in this module). Both are
    read off the Equal Error Rate operating point on this pooled VALIDATION
    evidence -- the same convention ``_risk_threshold_from_validation`` below
    uses to fix E1's comparison threshold.

    These are real measurements of the frozen profile, not a placeholder. A
    bare 0.0/0.0 pair here would be indistinguishable from a (fabricated)
    perfect result -- precisely what ADR-013 and
    ``tools/enrollment/activate.py`` were written to avoid, and this profile
    is written to the same ``model_profiles`` table via the same
    ``activate_profile`` path.
    """

    genuine_windows = validation_windows_by_user.get(user_id, [])
    other_windows = {uid: w for uid, w in validation_windows_by_user.items() if uid != user_id}
    genuine, impostor = _pooled_percentile_scores(
        user_id, frozen_profile, genuine_windows, other_windows
    )
    if len(genuine) == 0 or len(impostor) == 0:
        raise ValueError(
            "E1 could not measure the frozen profile's VALIDATION metrics: no "
            "genuine and/or impostor VALIDATION-partition scores were "
            f"available for user {user_id!r}"
        )
    eer = compute_eer(genuine, impostor)
    rates = compute_far_frr(genuine, impostor, eer.threshold)
    return ValidationMetrics(
        false_rejection_rate=rates.frr,
        false_acceptance_rate=rates.far,
    )


def _risk_threshold_from_validation(
    user_id: str,
    frozen_profile: dict[str, object],
    validation_windows_by_user: dict[str, list[FeatureWindow]],
) -> float:
    """A single fixed operating point, calibrated once on VALIDATION.

    E1 asks whether drift changes the FRR/FAR *at a fixed threshold* --
    recalibrating the threshold for every retrained model would hide
    exactly the drift the experiment is trying to measure. The threshold is
    calibrated from the frozen (first) profile only, exactly as an
    operational deployment would calibrate once at enrollment and then hold
    the operating point fixed.
    """

    genuine_windows = validation_windows_by_user.get(user_id, [])
    other_windows = {uid: w for uid, w in validation_windows_by_user.items() if uid != user_id}
    genuine, impostor = _pooled_percentile_scores(
        user_id, frozen_profile, genuine_windows, other_windows
    )
    if len(genuine) == 0 or len(impostor) == 0:
        raise ValueError(
            "E1 could not calibrate an operating threshold: no genuine and/or "
            f"impostor VALIDATION-partition scores were available for user {user_id!r}"
        )
    eer = compute_eer(genuine, impostor)
    risk_threshold = 1.0 - eer.threshold / 100.0
    return float(min(max(risk_threshold, _MIN_RISK), 1.0 - _MIN_RISK))


def run_drift_benefit(
    *,
    user_id: str,
    impostor_id: str,
    injected_segment_id: str,
    database: Path,
    manifest: Path,
    administration: Path,
    storage: StorageService,
    artifact_root: Path,
    ml_config: MLConfig,
    collection_settings: CollectionSettings,
    risk_settings: RiskSettings,
    evaluation_freeze: Path,
    config_directory: Path,
    dataset_manifest: Path,
    code_revision: str,
    update_settings: UpdateSettings | None = None,
    early_train_days: int | None = None,
) -> DriftBenefitResult:
    """Run E1 over one participant's frozen-corpus data.

    ``impostor_id`` and ``injected_segment_id`` are accepted, but unused
    here, so that the same experiment context this driver and
    ``tools.experiments.poisoning.run_poisoning_resistance`` both consume
    (design doc Section 8.6: "Both experiments replay the update manager
    across the frozen corpus") can be built once and passed to either.

    Steps: verify the evaluation freeze before touching anything; train a
    "frozen" profile on the earliest ``early_train_days`` TRAIN-partition
    days via the ADR-013 enrollment admission gate; submit and promote the
    *entire* TRAIN partition as one segment through the real
    ``UpdateManager``/``tools.updates.run.run_update`` path, past
    quarantine, to obtain the "updated" profile; score both, unmodified,
    against the same EVALUATION-partition windows at one threshold
    calibrated from the frozen profile's VALIDATION performance; and hand
    the paired risk arrays to ``evaluate_drift_benefit``.
    """

    verify_evaluation_freeze(
        evaluation_freeze,
        config_directory=config_directory,
        dataset_manifest=dataset_manifest,
        code_revision=code_revision,
    )

    settings = update_settings if update_settings is not None else load_update_settings()

    train_corpus = corpus_module.load_frozen_corpus(database, manifest, "TRAIN")
    validation_corpus = corpus_module.load_frozen_corpus(database, manifest, "VALIDATION")

    train_days = sorted(
        day
        for day, partition in train_corpus.day_assignments[user_id].items()
        if partition == "TRAIN"
    )
    if early_train_days is None:
        early_train_days = max(risk_settings.enrollment.min_distinct_days, len(train_days) // 2)
    if not (risk_settings.enrollment.min_distinct_days <= early_train_days < len(train_days)):
        raise ValueError(
            f"early_train_days={early_train_days!r} must leave at least one later "
            f"TRAIN day for the update; {len(train_days)} TRAIN days are available"
        )
    early_days = set(train_days[:early_train_days])

    all_train_windows = train_corpus.windows_by_user.get(user_id, [])
    early_windows = [w for w in all_train_windows if w.collection_day in early_days]

    consents, enrollments = load_administration_records(administration)
    admission = EnrollmentAdmission(
        settings=collection_settings,
        consent=consents.get(user_id),
        enrollment=enrollments.get(user_id),
        manifest_window_ids=train_corpus.manifest_window_ids,
        observed_at_by_window=train_corpus.observed_at_by_window,
        # TRAIN-scoped, from the ML config -- not the live state-machine
        # thresholds in config/risk.*.yaml (ADR-014).
        min_train_windows=int(ml_config.raw["enrollment"]["min_train_windows"]),
        min_train_distinct_days=int(ml_config.raw["enrollment"]["min_train_distinct_days"]),
        user_has_active_profile=_has_active_profile(storage, user_id),
        participant_id=user_id,
    )
    frozen_profile = train_user_profile(
        user_id, early_windows, ml_config, enrollment_admission=admission
    )
    if not frozen_profile:
        raise ValueError(
            f"E1 could not train a frozen profile for user {user_id!r} from "
            f"{len(early_windows)} early TRAIN windows"
        )

    frozen_keyboard = frozen_profile.get("keyboard")
    frozen_mouse = frozen_profile.get("mouse")
    if frozen_keyboard is not None:
        save_artifact(
            frozen_keyboard, artifact_root / user_id / f"{frozen_keyboard.version}.joblib"
        )
    if frozen_mouse is not None:
        save_artifact(frozen_mouse, artifact_root / user_id / f"{frozen_mouse.version}.joblib")

    frozen_profile_version = "e1-frozen-profile"
    frozen_metrics = _measure_frozen_profile_metrics(
        user_id, frozen_profile, validation_corpus.windows_by_user
    )
    frozen_model_profile = ModelProfile(
        profile_version=frozen_profile_version,
        user_id=user_id,
        keyboard_artifact_version=frozen_keyboard.version if frozen_keyboard else None,
        mouse_artifact_version=frozen_mouse.version if frozen_mouse else None,
        aggregate_checksum=_aggregate_checksum(
            frozen_profile_version, frozen_keyboard, frozen_mouse
        ),
        validation=ValidationReport(
            accepted=True,
            code="E1_FROZEN_PROFILE_NO_BASELINE",
            # No prior profile exists to regress against -- this is E1's
            # first ("frozen") profile for this user -- but
            # ValidationReport.baseline is structurally required, so the
            # same measured VALIDATION metrics are used for both baseline
            # and candidate. Same pattern as tools/enrollment/activate.py.
            baseline=frozen_metrics,
            candidate=frozen_metrics,
        ),
        created_at=_SEGMENT_COMPLETED_AT,
    )
    SQLiteUpdateRepository(storage).activate_profile(
        frozen_model_profile, retained_versions=settings.update_manager.retained_model_versions
    )

    threshold = _risk_threshold_from_validation(
        user_id, frozen_profile, validation_corpus.windows_by_user
    )

    # ``injected_segment_id`` (if present in this corpus at all) is a
    # poisoning-experiment artifact, not part of this user's genuine
    # collection history, and is excluded from the segment E1 promotes.
    genuine_train_windows = [
        window for window in all_train_windows if window.segment_id != injected_segment_id
    ]
    train_segment_ids = {window.segment_id for window in genuine_train_windows}
    if len(train_segment_ids) != 1:
        raise ValueError(
            "E1 expects the whole (non-injected) TRAIN partition to be exactly "
            f"one segment for user {user_id!r}; found {sorted(train_segment_ids)!r}"
        )
    train_segment_id = next(iter(train_segment_ids))
    scored_windows = sum(
        1
        for window in genuine_train_windows
        if window.keyboard_features is not None or window.mouse_features is not None
    )
    session_ids = {window.session_id for window in genuine_train_windows}
    verification = VerificationRecord(
        anchor_id=f"anchor-{train_segment_id}",
        user_id=user_id,
        session_id=next(iter(session_ids)),
        segment_id=train_segment_id,
        anchor_type=VerificationAnchor.A3_SCHEDULED_PROMPT,
        evidence_reference="e" * 64,
        authenticated_at=_SEGMENT_COMPLETED_AT,
    )
    evidence = SegmentEvidence(
        user_id=user_id,
        session_id=next(iter(session_ids)),
        segment_id=train_segment_id,
        completed_at=_SEGMENT_COMPLETED_AT,
        risk_levels=(RiskLevel.LOW,),
        scored_windows=scored_windows,
        enforcement_triggered=False,
        unexplained_gap=False,
        verification=verification,
    )
    manager = UpdateManager(settings, SQLiteUpdateRepository(storage))
    candidate = manager.submit_segment(evidence)
    if candidate.disposition.value != "QUARANTINED":
        raise ValueError(
            "E1 fixture segment did not clear the promotion gate's G1-G4 "
            f"conditions: disposition={candidate.disposition!r} "
            f"reason={candidate.reason_code!r}"
        )

    scheduled_for = _SEGMENT_COMPLETED_AT + timedelta(days=settings.update_manager.quarantine_days)
    outcome = run_update(
        user_id=user_id,
        database=database,
        manifest=manifest,
        storage=storage,
        artifact_root=artifact_root,
        ml_config=ml_config,
        scheduled_for=scheduled_for,
        update_settings=settings,
    )
    if outcome.status != "ACTIVATED":
        raise ValueError(
            "E1 could not produce an updated profile through the Model Update "
            f"Manager: run status={outcome.status!r} code={outcome.code!r}. "
            "The drift-benefit comparison requires a real update; it is never "
            "fabricated."
        )

    updated_provider = DirectoryProfileProvider(storage, artifact_root)
    updated_artifacts = updated_provider(user_id)
    if updated_artifacts is None:
        raise ValueError(f"updated profile for user {user_id!r} did not activate")
    updated_profile = {
        modality: artifact
        for modality, artifact in (
            ("keyboard", updated_artifacts.keyboard),
            ("mouse", updated_artifacts.mouse),
        )
        if artifact is not None
    }
    if set(updated_profile) != set(frozen_profile):
        raise ValueError(
            "E1 requires the frozen and updated profiles to have trained the "
            f"same modalities for a fair comparison: frozen={sorted(frozen_profile)!r} "
            f"updated={sorted(updated_profile)!r}"
        )

    evaluation_corpus = corpus_module.load_frozen_corpus(database, manifest, "EVALUATION")
    genuine_windows = evaluation_corpus.windows_by_user.get(user_id, [])
    impostor_windows = {
        other_id: windows
        for other_id, windows in evaluation_corpus.windows_by_user.items()
        if other_id != user_id
    }
    if not genuine_windows or not impostor_windows:
        raise ValueError(
            "E1 requires both genuine and cross-user impostor EVALUATION-"
            f"partition windows for user {user_id!r}"
        )

    frozen_genuine, frozen_impostor = _pooled_percentile_scores(
        user_id, frozen_profile, genuine_windows, impostor_windows
    )
    updated_genuine, updated_impostor = _pooled_percentile_scores(
        user_id, updated_profile, genuine_windows, impostor_windows
    )

    with (config_directory / "evaluation.development.yaml").open(encoding="utf-8") as handle:
        statistics = yaml.safe_load(handle)["statistics"]

    return evaluate_drift_benefit(
        frozen_genuine_risk=1.0 - frozen_genuine / 100.0,
        frozen_impostor_risk=1.0 - frozen_impostor / 100.0,
        updated_genuine_risk=1.0 - updated_genuine / 100.0,
        updated_impostor_risk=1.0 - updated_impostor / 100.0,
        threshold=threshold,
        bootstrap_resamples=int(statistics["bootstrap_resamples"]),
        bootstrap_confidence=float(statistics["bootstrap_confidence"]),
        random_seed=int(statistics["random_seed"]),
    )
