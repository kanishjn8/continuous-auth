"""E2 -- poisoning-resistance experiment driver (PLAN.md Section 12.4; design doc Section 8.6).

Submits a labelled impostor segment -- another participant's windows,
relabelled as the target user's own -- into the target's real candidate
stream alongside their genuine segment(s), then drives the actual
``UpdateManager``/``tools.updates.run.run_update`` promotion path
(``UpdateManager.submit_segment`` and ``reassess``, exercised for real, not
mocked) to see whether G1-G6 keep the injected segment out. The injected
segment carries no independent verification anchor (A1/A2/A3): that is the
one thing an attacker who has never authenticated as the target cannot
forge, and it is exactly what G2 exists to check.

The resulting candidates are handed to
``ml.experiments.update_manager.evaluate_poisoning_resistance``, which
itself raises ``AssertionError`` if any injected segment was ever promoted
-- this module never catches that exception. E2's whole point is that an
injected impostor segment must never reach promotion; if it ever does, this
must fail loudly, not log and continue.

RESULTS FROM THIS MODULE ARE NOT EVIDENCE about the real pilot cohort until
it has been run against the frozen ELIGIBLE corpus with the collection and
evaluation/config freezes verified, matching ``docs/update-manager.md``. A
run against the synthetic fixtures in ``tools/experiments/tests/`` exercises
the mechanics only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.decisions import VerificationRecord
from backend.app.risk.config import RiskSettings
from backend.app.storage.service import StorageService
from backend.app.updates.config import UpdateSettings, load_update_settings
from backend.app.updates.manager import SegmentEvidence, UpdateManager
from backend.app.updates.repository import SQLiteUpdateRepository
from ml.experiments.update_manager import PoisoningResistanceResult, evaluate_poisoning_resistance
from ml.features.config import MLConfig
from protocol.generated.python.contracts import RiskLevel, VerificationAnchor
from tools.collection import corpus as corpus_module
from tools.collection.config import CollectionSettings
from tools.evaluation.freeze import verify_evaluation_freeze
from tools.updates.run import run_update

_SEGMENT_COMPLETED_AT = datetime(2026, 9, 5, tzinfo=UTC)


def run_poisoning_resistance(
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
    segment_completed_at: datetime = _SEGMENT_COMPLETED_AT,
) -> PoisoningResistanceResult:
    """Run E2 over one participant's frozen-corpus data.

    ``impostor_id``, ``administration``, ``collection_settings``, and
    ``risk_settings`` are accepted, but unused here, so that the same
    experiment context ``tools.experiments.drift.run_drift_benefit`` and
    this driver both consume (design doc Section 8.6) can be built once and
    passed to either.

    Every distinct segment found in the target user's TRAIN partition is
    submitted as a candidate: the segment named ``injected_segment_id``
    carries no verification anchor (an attacker cannot forge one), every
    other segment carries a genuine A3 anchor. All of them are then run
    through one real scheduled update, past quarantine, via
    ``tools.updates.run.run_update`` -- the same production entry point
    that reassesses, promotes, trains, validates, and activates. The final
    candidate dispositions are read back from the repository and handed to
    ``evaluate_poisoning_resistance``.
    """

    verify_evaluation_freeze(
        evaluation_freeze,
        config_directory=config_directory,
        dataset_manifest=dataset_manifest,
        code_revision=code_revision,
    )

    settings = update_settings if update_settings is not None else load_update_settings()
    repository = SQLiteUpdateRepository(storage)
    manager = UpdateManager(settings, repository)

    train_corpus = corpus_module.load_frozen_corpus(database, manifest, "TRAIN")
    train_windows = train_corpus.windows_by_user.get(user_id, [])
    if not train_windows:
        raise ValueError(f"E2 found no TRAIN-partition windows for user {user_id!r}")

    segment_ids = sorted({window.segment_id for window in train_windows})
    if injected_segment_id not in segment_ids:
        raise ValueError(
            f"E2's injected_segment_id {injected_segment_id!r} was not found among "
            f"user {user_id!r}'s TRAIN-partition segments {segment_ids!r}"
        )

    submitted = []
    for segment_id in segment_ids:
        segment_windows = [w for w in train_windows if w.segment_id == segment_id]
        session_id = next(iter({w.session_id for w in segment_windows}))
        scored_windows = sum(
            1
            for w in segment_windows
            if w.keyboard_features is not None or w.mouse_features is not None
        )
        is_injected = segment_id == injected_segment_id
        verification = (
            None
            if is_injected
            else VerificationRecord(
                anchor_id=f"anchor-{segment_id}",
                user_id=user_id,
                session_id=session_id,
                segment_id=segment_id,
                anchor_type=VerificationAnchor.A3_SCHEDULED_PROMPT,
                evidence_reference="c" * 64,
                authenticated_at=segment_completed_at,
            )
        )
        evidence = SegmentEvidence(
            user_id=user_id,
            session_id=session_id,
            segment_id=segment_id,
            completed_at=segment_completed_at,
            risk_levels=(RiskLevel.LOW,),
            scored_windows=scored_windows,
            enforcement_triggered=False,
            unexplained_gap=False,
            verification=verification,
        )
        candidate = manager.submit_segment(evidence)
        submitted.append(candidate)
        if is_injected and candidate.disposition.value != "REJECTED":
            raise AssertionError(
                "poisoning-resistance gate failed at submission: an injected "
                f"segment {segment_id!r} was not rejected immediately "
                f"(disposition={candidate.disposition!r})"
            )

    scheduled_for = segment_completed_at + timedelta(days=settings.update_manager.quarantine_days)
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
    if outcome.status not in {"ACTIVATED", "REJECTED", "SKIPPED"}:
        raise ValueError(
            f"E2's scheduled update run failed unexpectedly: status={outcome.status!r} "
            f"code={outcome.code!r}"
        )

    final_candidates = repository.candidates_for_user(user_id)
    return evaluate_poisoning_resistance(
        list(final_candidates), injected_segment_ids={injected_segment_id}
    )
