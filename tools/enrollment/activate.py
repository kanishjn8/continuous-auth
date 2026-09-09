"""Train and activate a participant's first model profile (ADR-013).

This is the reviewed replacement for tools/demo/bootstrap_first_model.py on
participant data. The demo script stays in place for synthetic development.

Two honest differences from the demo bootstrap:

* Training data passes the ADR-013 enrollment admission gate rather than
  skipping the boundary because its provenance happens to be SYNTHETIC.
* The activation ValidationReport carries only numbers that were actually
  measured. There are two paths, chosen automatically by whether the frozen
  manifest contained another participant -- no flag, no config, no operator
  choice:

  - **Cohort present.** FRR and FAR are both measured on the held-out
    VALIDATION partition at the pooled Equal Error Rate threshold, FAR by
    zero-effort cross-evaluation against the other participants. Reason code
    ``ENROLLMENT_INITIAL_PROFILE_NO_BASELINE``.
  - **No cohort (ADR-014, the delivered single-participant build).** FRR is
    measured on the same held-out partition, at the operating point the
    deployed system actually uses (``risk.medium_threshold`` on the
    calibrated risk scale). FAR is recorded as ``None``, meaning **not
    measured** -- a false-acceptance rate needs impostor data, and a
    single-participant corpus contains none. It is never ``0.0``, never
    estimated, never imputed. Reason code
    ``ENROLLMENT_INITIAL_PROFILE_NO_IMPOSTOR_COHORT``.

  Genuine scores are mandatory in both paths: no scorable VALIDATION window
  raises ``ENROLLMENT_VALIDATION_NO_GENUINE`` and activation stops.

  The EVALUATION partition is never read: it is reserved for the headline
  result, and touching it here would make that result untrustworthy.

The reason code states plainly that no prior profile existed to regress
against, and ``ValidationReport.operating_point`` states plainly where the
numbers came from and what was not measured. Those are facts about
enrollment, not defects, and they must not be disguised as a passing
regression check.

Artifacts are written to disk only after validation has succeeded, so a
failed activation leaves no orphaned ``.joblib`` with no database row
pointing at it.

Two configuration gaps are deliberately left open rather than papered over:

* ``--ml-config`` and ``--risk-config`` have no default. Unlike
  ``config/storage.pilot.yaml`` and ``config/collection.pilot.yaml``, there is
  no ``config/ml.pilot.yaml`` or ``config/risk.pilot.yaml`` in this
  repository. ``config/risk.development.yaml`` itself says its numeric
  values ("the O3/O7/O8/O9 questions") "remain OPEN and require Phase 4/5
  evidence plus human approval" -- an approved pilot risk policy has not been
  written, so this module does not guess one on the operator's behalf. The
  operator must name a config explicitly.
* There is no ``config/updates.pilot.yaml`` to source ``retained_model_versions``
  from (creating one is out of scope here, and `backend/app/updates/config.py`
  requires ``development_only: true`` for any file it accepts, which a real
  pilot policy file cannot honestly claim). ``retained_model_versions``
  is therefore a keyword-only parameter with a default of 3 -- the same
  value documented in ``config/updates.development.yaml`` today -- callable
  sites may override it once an approved pilot policy exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from backend.app.models.service import percentile_for_calibrated_risk
from backend.app.risk.config import RiskSettings, load_risk_settings
from backend.app.storage.config import load_storage_settings
from backend.app.storage.service import StorageService
from backend.app.updates.manager import ModelProfile, ValidationMetrics, ValidationReport
from backend.app.updates.repository import SQLiteUpdateRepository
from ml.evaluation.cross_evaluation import zero_effort_cross_evaluation
from ml.evaluation.metrics import compute_eer, compute_far_frr
from ml.features.config import MLConfig
from ml.features.config import load_config as load_ml_config
from ml.training.common import ModelArtifact
from ml.training.enrollment import EnrollmentAdmission
from ml.training.isolation_forest import train_user_profile
from ml.training.persistence import save_artifact
from tools.collection import corpus as corpus_module
from tools.collection.config import CollectionSettings, load_collection_settings
from tools.collection.repository import load_administration_records

ROOT = Path(__file__).resolve().parents[2]


def _has_active_profile(storage: StorageService, participant_id: str) -> bool:
    with storage.database.connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM model_profiles WHERE user_id = ? AND status = 'ACTIVE'",
            (participant_id,),
        ).fetchone()
    return row is not None


def _aggregate_checksum(
    profile_version: str,
    keyboard_artifact: ModelArtifact | None,
    mouse_artifact: ModelArtifact | None,
) -> str:
    values = (
        profile_version,
        keyboard_artifact.version if keyboard_artifact else "",
        keyboard_artifact.checksum if keyboard_artifact else "",
        mouse_artifact.version if mouse_artifact else "",
        mouse_artifact.checksum if mouse_artifact else "",
    )
    return hashlib.sha256("\x00".join(values).encode()).hexdigest()


def _measure_validation_metrics(
    participant_id: str,
    profile: dict[str, ModelArtifact],
    validation_windows_by_user: dict[str, list],
    *,
    medium_threshold: float,
) -> tuple[ValidationMetrics, str]:
    """Measure FRR always; measure FAR only when impostor evidence exists.

    Genuine scores are **mandatory**: a VALIDATION partition that cannot
    produce one is a data problem and must stop activation. Impostor scores
    are **not** mandatory, because a single-participant corpus can never
    contain any -- and an absent cohort is a fact to record, never a number
    to invent (ADR-014).

    Every trained modality (keyboard, mouse, or both) contributes its
    percentile scores to one pooled genuine/impostor pair. Percentile scores
    share one calibrated 0-100 scale by construction
    (``ml/calibration/percentile.py``), so pooling across modalities before
    finding a single operating point is a legitimate combination, not an
    apples-to-oranges average -- and it avoids inventing a keyboard/mouse
    fusion weight that ADR-013 never specifies.

    **With impostors** the operating threshold is the Equal Error Rate point
    on that pooled evidence -- an evaluation convention already established
    in ``ml/evaluation/pipeline.py`` -- and both rates are measured.

    **Without impostors there is no EER**, so FRR is measured at the
    operating point the deployed system actually uses. The chain is:

        ml/calibration/percentile.py      -> normality percentile, 0..100
        backend/app/models/service.py     -> calibrated_risk = 1 - pct/100
        RiskEngine._fusion                -> availability-weighted mean
        RiskEngine._candidate_level       -> breach when value >= medium_threshold

    so a window breaches MEDIUM exactly when its percentile is **at or
    below** ``percentile_for_calibrated_risk(medium_threshold)``. The
    inclusive comparison flips sides with the decreasing mapping, which is
    why the boundary case is asserted in the tests.

    One honest limitation, which belongs next to the number wherever it is
    reported: this is the **per-window** rate at the deployed threshold. The
    live engine additionally applies context confidence, EWMA smoothing, and
    K-of-N breach counting before it raises a risk level, all of which
    suppress isolated breaches. The measured value is therefore an upper
    bound on the rate at which the live system would actually escalate, not
    an estimate of it.
    """

    genuine: list[float] = []
    impostor: list[float] = []
    for artifact in profile.values():
        cross_results = zero_effort_cross_evaluation(
            {participant_id: artifact}, validation_windows_by_user
        )
        result = cross_results.get(participant_id)
        if result is None:
            continue
        genuine.extend(result.genuine_scores)
        impostor.extend(result.all_impostor_scores().tolist())

    if not genuine:
        raise ValueError(
            "ENROLLMENT_VALIDATION_NO_GENUINE: no scorable VALIDATION-partition "
            f"windows were available for participant {participant_id!r}; a first "
            "profile is never activated without a real genuine measurement. This "
            "is a data problem, not a cohort problem -- check the freeze manifest's "
            "VALIDATION partition and the quality gate."
        )

    genuine_arr = np.asarray(genuine, dtype=float)
    if impostor:
        impostor_arr = np.asarray(impostor, dtype=float)
        eer = compute_eer(genuine_arr, impostor_arr)
        rates = compute_far_frr(genuine_arr, impostor_arr, eer.threshold)
        return (
            ValidationMetrics(
                false_rejection_rate=rates.frr,
                false_acceptance_rate=rates.far,
            ),
            "pooled-EER threshold on VALIDATION with cross-user impostors",
        )

    return _frr_at_deployed_operating_point(genuine_arr, medium_threshold)


def _frr_at_deployed_operating_point(
    genuine_percentiles: np.ndarray,
    medium_threshold: float,
) -> tuple[ValidationMetrics, str]:
    """FRR at the live MEDIUM boundary, with FAR explicitly unmeasured.

    Split out from :func:`_measure_validation_metrics` so the boundary
    semantics can be asserted directly, without stubbing the scorer: the
    comparison is **inclusive** and on the *low* side of the percentile
    scale, because ``calibrated_risk = 1 - percentile/100`` is decreasing and
    ``RiskEngine._candidate_level`` breaches on ``>=``.
    """

    percentile_threshold = percentile_for_calibrated_risk(medium_threshold)
    frr = float(np.mean(genuine_percentiles <= percentile_threshold))
    operating_point = (
        f"frr@calibrated_risk>={medium_threshold:.3f} (risk.medium_threshold; "
        f"percentile<={percentile_threshold:.3f}), per-window before smoothing "
        "and K-of-N; far=unmeasured (no impostor cohort in the frozen manifest)"
    )
    return (
        ValidationMetrics(false_rejection_rate=frr, false_acceptance_rate=None),
        operating_point,
    )


def activate_first_profile(
    *,
    participant_id: str,
    database: Path,
    manifest: Path,
    administration: Path,
    artifact_root: Path,
    storage: StorageService,
    collection_settings: CollectionSettings,
    ml_config: MLConfig,
    risk_settings: RiskSettings,
    retained_model_versions: int = 3,
) -> ModelProfile:
    """Train and activate ``participant_id``'s first profile from the frozen corpus.

    Trains on the TRAIN partition only, admitted through
    ``require_enrollment_admission`` (never bypassed), measures FRR on
    VALIDATION and FAR by zero-effort cross-evaluation against other
    participants' VALIDATION windows, and activates through
    ``SQLiteUpdateRepository.activate_profile`` -- the same audited path the
    Model Update Manager uses. The EVALUATION partition is never loaded.
    """

    # Module-level access, never a direct `from tools.collection.corpus import
    # load_frozen_corpus` -- callers that need to observe or intercept every
    # partition request (this module's own tests included) patch the module
    # attribute, which only works if this call resolves it at call time.
    train_corpus = corpus_module.load_frozen_corpus(database, manifest, "TRAIN")
    validation_corpus = corpus_module.load_frozen_corpus(database, manifest, "VALIDATION")

    consents, enrollments = load_administration_records(administration)
    has_active_profile = _has_active_profile(storage, participant_id)

    admission = EnrollmentAdmission(
        settings=collection_settings,
        consent=consents.get(participant_id),
        enrollment=enrollments.get(participant_id),
        manifest_window_ids=train_corpus.manifest_window_ids,
        observed_at_by_window=train_corpus.observed_at_by_window,
        # TRAIN-scoped training-admission policy, from the ML config. These
        # used to be sourced from risk_settings.enrollment.*, which is the
        # LIVE state-machine policy counted over the user's whole observed
        # history -- a different question with a different correct answer.
        # Applying it to a 60/20/20 TRAIN partition silently demanded roughly
        # twice as many collection days as any document stated (ADR-014).
        min_train_windows=int(ml_config.raw["enrollment"]["min_train_windows"]),
        min_train_distinct_days=int(ml_config.raw["enrollment"]["min_train_distinct_days"]),
        user_has_active_profile=has_active_profile,
        participant_id=participant_id,
    )

    train_windows = train_corpus.windows_by_user.get(participant_id, [])
    profile_artifacts = train_user_profile(
        participant_id, train_windows, ml_config, enrollment_admission=admission
    )
    if not profile_artifacts:
        raise ValueError(
            f"neither modality trained a model for participant {participant_id!r}: "
            f"{len(train_windows)} eligible TRAIN windows did not clear "
            "min_baseline_windows for either keyboard or mouse"
        )

    keyboard_artifact = profile_artifacts.get("keyboard")
    mouse_artifact = profile_artifacts.get("mouse")

    # Measure BEFORE persisting anything. Training and calibration happen in
    # memory above; if validation raises (no genuine VALIDATION windows, say),
    # a failed activation must leave no .joblib on disk with no database row
    # pointing at it. Artifacts are written below, immediately before the
    # profile row that references them.
    metrics, operating_point = _measure_validation_metrics(
        participant_id,
        profile_artifacts,
        validation_corpus.windows_by_user,
        medium_threshold=risk_settings.risk.medium_threshold,
    )

    if keyboard_artifact is not None:
        save_artifact(
            keyboard_artifact,
            artifact_root / participant_id / f"{keyboard_artifact.version}.joblib",
        )
    if mouse_artifact is not None:
        save_artifact(
            mouse_artifact,
            artifact_root / participant_id / f"{mouse_artifact.version}.joblib",
        )

    profile_version = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%f")
    profile = ModelProfile(
        profile_version=profile_version,
        user_id=participant_id,
        keyboard_artifact_version=keyboard_artifact.version if keyboard_artifact else None,
        mouse_artifact_version=mouse_artifact.version if mouse_artifact else None,
        aggregate_checksum=_aggregate_checksum(profile_version, keyboard_artifact, mouse_artifact),
        validation=ValidationReport(
            # accepted=True gates ACTIVATION, and a first profile's acceptance
            # criterion is the ADR-013 enrollment gate (consent, corpus
            # integrity, TRAIN volume and day coverage) that
            # require_enrollment_admission already enforced above -- not a
            # regression check, because there is no prior profile to regress
            # against. An UPDATE means something different and refuses an
            # unmeasured FAR outright; see
            # backend/app/updates/manager.py::UpdateManager._validate.
            accepted=True,
            code=(
                "ENROLLMENT_INITIAL_PROFILE_NO_BASELINE"
                if metrics.false_acceptance_rate is not None
                else "ENROLLMENT_INITIAL_PROFILE_NO_IMPOSTOR_COHORT"
            ),
            # ValidationReport.baseline is structurally required, but there
            # is no prior profile for this participant to regress against --
            # this is their first profile. The same measured VALIDATION
            # metrics are therefore used for both baseline and candidate;
            # the reason code says so explicitly so this is never mistaken
            # for a real prior-vs-candidate regression check.
            baseline=metrics,
            candidate=metrics,
            operating_point=operating_point,
        ),
        created_at=datetime.now(UTC),
    )

    SQLiteUpdateRepository(storage).activate_profile(
        profile, retained_versions=retained_model_versions
    )
    return profile


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train and activate a participant's first model profile from the frozen corpus"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    activate = subparsers.add_parser("activate")
    activate.add_argument("--participant-id", required=True)
    activate.add_argument("--database", type=Path, required=True)
    activate.add_argument("--manifest", type=Path, required=True)
    activate.add_argument("--administration", type=Path, required=True)
    activate.add_argument("--artifact-root", type=Path, required=True)
    activate.add_argument("--storage-config", type=Path, default=ROOT / "config/storage.pilot.yaml")
    activate.add_argument(
        "--collection-config", type=Path, default=ROOT / "config/collection.pilot.yaml"
    )
    # No default: see the module docstring. Neither an approved
    # config/ml.pilot.yaml nor config/risk.pilot.yaml exists yet.
    activate.add_argument("--ml-config", type=Path, required=True)
    activate.add_argument("--risk-config", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    storage_settings = load_storage_settings(args.storage_config, workspace_root=ROOT)
    storage = StorageService.open(storage_settings)
    collection_settings = load_collection_settings(args.collection_config)
    ml_config = load_ml_config(args.ml_config)
    risk_settings = load_risk_settings(args.risk_config)

    profile = activate_first_profile(
        participant_id=args.participant_id,
        database=args.database,
        manifest=args.manifest,
        administration=args.administration,
        artifact_root=args.artifact_root,
        storage=storage,
        collection_settings=collection_settings,
        ml_config=ml_config,
        risk_settings=risk_settings,
    )

    print(
        json.dumps(
            {
                "profile_version": profile.profile_version,
                "user_id": profile.user_id,
                "keyboard_artifact_version": profile.keyboard_artifact_version,
                "mouse_artifact_version": profile.mouse_artifact_version,
                "validation_code": profile.validation.code,
                "validation_frr": profile.validation.candidate.false_rejection_rate,
                # null means NOT MEASURED, never zero. See the module docstring.
                "validation_far": profile.validation.candidate.false_acceptance_rate,
                "validation_operating_point": profile.validation.operating_point,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
