from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.app.decisions import VerificationRecord
from backend.app.updates import (
    CandidateBuild,
    ModelProfile,
    SegmentEvidence,
    UpdateManager,
    ValidationMetrics,
    load_update_settings,
)
from protocol.generated.python.contracts import (
    CandidateDisposition,
    RiskLevel,
    UpdateCandidate,
    VerificationAnchor,
)


class MemoryUpdateRepository:
    def __init__(self) -> None:
        self.candidates: dict[str, UpdateCandidate] = {}
        self.runs: dict[str, str] = {}
        self.active: ModelProfile | None = None
        self.retained: ModelProfile | None = None

    def save_candidate(self, candidate: UpdateCandidate) -> None:
        self.candidates[candidate.candidate_id] = candidate

    def candidates_for_user(self, user_id: str) -> tuple[UpdateCandidate, ...]:
        return tuple(value for value in self.candidates.values() if value.user_id == user_id)

    def schedule_due(self, user_id: str, now: datetime, cadence: timedelta) -> bool:
        del user_id, now, cadence
        return True

    def begin_run(self, run_id: str, user_id: str, scheduled_for: datetime) -> None:
        del user_id, scheduled_for
        self.runs[run_id] = "RUNNING"

    def finish_run(self, run_id: str, status: str, report: dict[str, object]) -> None:
        del report
        self.runs[run_id] = status

    def activate_profile(self, profile: ModelProfile, retained_versions: int) -> None:
        del retained_versions
        self.retained = self.active
        self.active = profile

    def rollback(self, user_id: str, occurred_at: datetime) -> ModelProfile:
        del occurred_at
        if self.active is None or self.retained is None or self.active.user_id != user_id:
            raise RuntimeError("rollback unavailable")
        self.active, self.retained = self.retained, self.active
        return self.active


def _verification(
    anchor: VerificationAnchor = VerificationAnchor.A3_SCHEDULED_PROMPT,
) -> VerificationRecord:
    return VerificationRecord(
        anchor_id="anchor-1",
        user_id="synthetic-user",
        session_id="session-1",
        segment_id="segment-1",
        anchor_type=anchor,
        evidence_reference="a" * 64,
        authenticated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _evidence(**changes: object) -> SegmentEvidence:
    values: dict[str, object] = {
        "user_id": "synthetic-user",
        "session_id": "session-1",
        "segment_id": "segment-1",
        "completed_at": datetime(2026, 1, 1, tzinfo=UTC),
        "risk_levels": (RiskLevel.LOW, RiskLevel.MEDIUM),
        "scored_windows": 10,
        "enforcement_triggered": False,
        "unexplained_gap": False,
        "verification": _verification(),
    }
    values.update(changes)
    return SegmentEvidence(**values)  # type: ignore[arg-type]


def test_each_fundamental_gate_can_reject_and_low_risk_is_not_an_anchor() -> None:
    cases = (
        _evidence(risk_levels=(RiskLevel.HIGH,)),
        _evidence(verification=None),
        _evidence(scored_windows=9),
        _evidence(unexplained_gap=True),
    )
    for index, evidence in enumerate(cases):

        def candidate_id_factory(index: int = index) -> str:
            return f"candidate-{index}"

        repository = MemoryUpdateRepository()
        manager = UpdateManager(
            load_update_settings(),
            repository,
            candidate_id_factory=candidate_id_factory,
        )
        candidate = manager.submit_segment(evidence)
        assert candidate.disposition is CandidateDisposition.REJECTED
        if evidence.verification is None:
            assert candidate.verification_anchor is None


def test_quarantine_schedule_validation_activation_and_rollback() -> None:
    repository = MemoryUpdateRepository()
    manager = UpdateManager(
        load_update_settings(),
        repository,
        candidate_id_factory=lambda: "candidate-1",
        run_id_factory=lambda: "run-1",
    )
    candidate = manager.submit_segment(_evidence())
    assert candidate.disposition is CandidateDisposition.QUARANTINED
    too_early = manager.reassess(
        candidate, now=datetime(2026, 1, 7, tzinfo=UTC), scheduled_run=True
    )
    assert too_early.disposition is CandidateDisposition.QUARANTINED
    scheduled = datetime(2026, 1, 8, tzinfo=UTC)

    def build(user_id: str, promoted: tuple[UpdateCandidate, ...]) -> CandidateBuild:
        assert user_id == "synthetic-user"
        assert promoted[0].disposition is CandidateDisposition.PROMOTED
        return CandidateBuild(
            profile_version="profile-2",
            keyboard_artifact_version="keyboard-2",
            keyboard_checksum="b" * 64,
            mouse_artifact_version=None,
            mouse_checksum=None,
            baseline_metrics=ValidationMetrics(0.10, 0.05),
            candidate_metrics=ValidationMetrics(0.09, 0.05),
        )

    outcome = manager.run_scheduled(
        user_id="synthetic-user", scheduled_for=scheduled, candidate_builder=build
    )
    assert outcome.status == "ACTIVATED"
    assert repository.active is not None
    assert repository.runs["run-1"] == "ACTIVATED"


def test_regression_rejects_without_changing_active_profile() -> None:
    repository = MemoryUpdateRepository()
    manager = UpdateManager(
        load_update_settings(), repository, candidate_id_factory=lambda: "candidate-1"
    )
    candidate = manager.submit_segment(_evidence())
    repository.candidates[candidate.candidate_id] = manager.reassess(
        candidate, now=datetime(2026, 1, 8, tzinfo=UTC), scheduled_run=True
    )

    def build(user_id: str, promoted: tuple[UpdateCandidate, ...]) -> CandidateBuild:
        del user_id, promoted
        return CandidateBuild(
            profile_version="regressed",
            keyboard_artifact_version="keyboard-bad",
            keyboard_checksum="c" * 64,
            mouse_artifact_version=None,
            mouse_checksum=None,
            baseline_metrics=ValidationMetrics(0.10, 0.05),
            candidate_metrics=ValidationMetrics(0.20, 0.20),
        )

    outcome = manager.run_scheduled(
        user_id="synthetic-user",
        scheduled_for=datetime(2026, 1, 8, tzinfo=UTC),
        candidate_builder=build,
    )
    assert outcome.status == "REJECTED"
    assert outcome.code == "VALIDATION_REGRESSION"
    assert repository.active is None
    assert repository.candidates["candidate-1"].disposition is CandidateDisposition.REJECTED
    assert repository.candidates["candidate-1"].reason_code == "MODEL_VALIDATION_REGRESSION"


def test_failed_builder_keeps_promoted_candidate_for_a_future_scheduled_retry() -> None:
    repository = MemoryUpdateRepository()
    manager = UpdateManager(
        load_update_settings(), repository, candidate_id_factory=lambda: "candidate-1"
    )
    manager.submit_segment(_evidence())

    def fail(user_id: str, promoted: tuple[UpdateCandidate, ...]) -> CandidateBuild:
        del user_id, promoted
        raise RuntimeError("synthetic build failure")

    outcome = manager.run_scheduled(
        user_id="synthetic-user",
        scheduled_for=datetime(2026, 1, 8, tzinfo=UTC),
        candidate_builder=fail,
    )
    assert outcome.status == "FAILED"
    assert repository.candidates["candidate-1"].disposition is CandidateDisposition.PROMOTED


def test_validation_metrics_accepts_unmeasured_far() -> None:
    """``None`` means NOT MEASURED. It is a legal value; ``0.0`` is a claim."""
    metrics = ValidationMetrics(false_rejection_rate=0.12, false_acceptance_rate=None)
    assert metrics.false_acceptance_rate is None
    assert metrics.false_acceptance_rate != 0.0


def test_validation_metrics_still_range_checks_a_measured_far() -> None:
    with pytest.raises(ValueError, match="false acceptance rate"):
        ValidationMetrics(false_rejection_rate=0.1, false_acceptance_rate=1.5)


def test_update_is_rejected_when_far_is_unmeasured() -> None:
    """An update means "not worse than the active profile".

    Without a FAR on both sides that comparison is undecidable, so the honest
    answer is refusal -- never a substituted zero.
    """
    manager = UpdateManager(load_update_settings(), MemoryUpdateRepository())
    build = CandidateBuild(
        profile_version="v2",
        keyboard_artifact_version="keyboard-2",
        keyboard_checksum="a" * 64,
        mouse_artifact_version=None,
        mouse_checksum=None,
        baseline_metrics=ValidationMetrics(0.10, None),
        candidate_metrics=ValidationMetrics(0.09, None),
    )
    report = manager._validate(build)
    assert report.accepted is False
    assert report.code == "VALIDATION_FAR_UNMEASURED"


def test_update_is_rejected_when_only_one_side_has_a_measured_far() -> None:
    manager = UpdateManager(load_update_settings(), MemoryUpdateRepository())
    build = CandidateBuild(
        profile_version="v2",
        keyboard_artifact_version="keyboard-2",
        keyboard_checksum="a" * 64,
        mouse_artifact_version=None,
        mouse_checksum=None,
        baseline_metrics=ValidationMetrics(0.10, 0.05),
        candidate_metrics=ValidationMetrics(0.09, None),
    )
    report = manager._validate(build)
    assert report.accepted is False
    assert report.code == "VALIDATION_FAR_UNMEASURED"


def test_unmeasured_far_rejects_the_run_without_activating() -> None:
    repository = MemoryUpdateRepository()
    manager = UpdateManager(
        load_update_settings(), repository, candidate_id_factory=lambda: "candidate-1"
    )
    candidate = manager.submit_segment(_evidence())
    repository.candidates[candidate.candidate_id] = manager.reassess(
        candidate, now=datetime(2026, 1, 8, tzinfo=UTC), scheduled_run=True
    )

    def build(user_id: str, promoted: tuple[UpdateCandidate, ...]) -> CandidateBuild:
        del user_id, promoted
        return CandidateBuild(
            profile_version="unmeasurable",
            keyboard_artifact_version="keyboard-2",
            keyboard_checksum="d" * 64,
            mouse_artifact_version=None,
            mouse_checksum=None,
            baseline_metrics=ValidationMetrics(0.10, None),
            candidate_metrics=ValidationMetrics(0.09, None),
        )

    outcome = manager.run_scheduled(
        user_id="synthetic-user",
        scheduled_for=datetime(2026, 1, 8, tzinfo=UTC),
        candidate_builder=build,
    )
    assert outcome.status == "REJECTED"
    assert outcome.code == "VALIDATION_FAR_UNMEASURED"
    assert repository.active is None
