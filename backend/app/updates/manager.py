"""Segment promotion, quarantine, scheduled validation, activation, and rollback."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from backend.app.decisions.adapters import VerificationRecord
from protocol.generated.python.contracts import (
    PROTOCOL_VERSION,
    CandidateDisposition,
    GateEvidence,
    RiskLevel,
    UpdateCandidate,
    VerificationAnchor,
)

from .config import UpdateSettings


@dataclass(frozen=True)
class SegmentEvidence:
    user_id: str
    session_id: str
    segment_id: str
    completed_at: datetime
    risk_levels: tuple[RiskLevel, ...]
    scored_windows: int
    enforcement_triggered: bool
    unexplained_gap: bool
    verification: VerificationRecord | None

    def __post_init__(self) -> None:
        if not self.user_id.strip() or not self.session_id.strip() or not self.segment_id.strip():
            raise ValueError("segment evidence identifiers must not be blank")
        if self.scored_windows < 0:
            raise ValueError("scored window count must be non-negative")
        if self.completed_at.tzinfo is None:
            raise ValueError("segment completion time must be timezone-aware")


@dataclass(frozen=True)
class ValidationMetrics:
    false_rejection_rate: float
    #: ``None`` means NOT MEASURED -- there was no impostor evidence to measure
    #: against. It never means "zero". A false-acceptance rate requires impostor
    #: data, and a single-participant corpus contains none by construction
    #: (ADR-014). Any consumer that needs a FAR comparison must refuse rather
    #: than substitute a value; see ``UpdateManager._validate``.
    false_acceptance_rate: float | None

    def __post_init__(self) -> None:
        if not 0 <= self.false_rejection_rate <= 1:
            raise ValueError("false rejection rate must be in [0, 1]")
        if self.false_acceptance_rate is None:
            return
        if not 0 <= self.false_acceptance_rate <= 1:
            raise ValueError("false acceptance rate must be in [0, 1]")


@dataclass(frozen=True)
class ValidationReport:
    accepted: bool
    code: str
    baseline: ValidationMetrics
    candidate: ValidationMetrics
    #: Free-form audit string naming the operating point the metrics were
    #: measured at, and stating plainly when FAR was not measured. Serialised
    #: into the existing ``model_profiles.validation_json`` column, so it needs
    #: no migration; profiles written before it existed load with "".
    operating_point: str = ""


@dataclass(frozen=True)
class CandidateBuild:
    profile_version: str
    keyboard_artifact_version: str | None
    keyboard_checksum: str | None
    mouse_artifact_version: str | None
    mouse_checksum: str | None
    baseline_metrics: ValidationMetrics
    candidate_metrics: ValidationMetrics

    def __post_init__(self) -> None:
        if not self.profile_version.strip():
            raise ValueError("candidate profile version must not be blank")
        if self.keyboard_artifact_version is None and self.mouse_artifact_version is None:
            raise ValueError("candidate profile must contain at least one modality artifact")
        for modality, version, checksum in (
            ("keyboard", self.keyboard_artifact_version, self.keyboard_checksum),
            ("mouse", self.mouse_artifact_version, self.mouse_checksum),
        ):
            if (version is None) != (checksum is None):
                raise ValueError(f"{modality} artifact version and checksum must occur together")
            if checksum is not None and (
                len(checksum) != 64
                or any(character not in "0123456789abcdef" for character in checksum)
            ):
                raise ValueError(f"{modality} artifact checksum must be lowercase SHA-256")

    @property
    def aggregate_checksum(self) -> str:
        values = (
            self.profile_version,
            self.keyboard_artifact_version or "",
            self.keyboard_checksum or "",
            self.mouse_artifact_version or "",
            self.mouse_checksum or "",
        )
        return hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ModelProfile:
    profile_version: str
    user_id: str
    keyboard_artifact_version: str | None
    mouse_artifact_version: str | None
    aggregate_checksum: str
    validation: ValidationReport
    created_at: datetime


@dataclass(frozen=True)
class UpdateRunOutcome:
    run_id: str
    status: str
    code: str
    profile: ModelProfile | None


class UpdateRepository(Protocol):
    def save_candidate(self, candidate: UpdateCandidate) -> None: ...

    def candidates_for_user(self, user_id: str) -> tuple[UpdateCandidate, ...]: ...

    def schedule_due(self, user_id: str, now: datetime, cadence: timedelta) -> bool: ...

    def begin_run(self, run_id: str, user_id: str, scheduled_for: datetime) -> None: ...

    def finish_run(self, run_id: str, status: str, report: dict[str, object]) -> None: ...

    def activate_profile(self, profile: ModelProfile, retained_versions: int) -> None: ...

    def rollback(self, user_id: str, occurred_at: datetime) -> ModelProfile: ...


CandidateBuilder = Callable[[str, tuple[UpdateCandidate, ...]], CandidateBuild]


def _candidate_id() -> str:
    return f"candidate-{uuid.uuid4()}"


def _run_id() -> str:
    return f"update-run-{uuid.uuid4()}"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class UpdateManager:
    def __init__(
        self,
        settings: UpdateSettings,
        repository: UpdateRepository,
        *,
        candidate_id_factory: Callable[[], str] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self._candidate_id_factory = candidate_id_factory or _candidate_id
        self._run_id_factory = run_id_factory or _run_id

    def _verification_anchor(self, evidence: SegmentEvidence) -> VerificationAnchor | None:
        verification = evidence.verification
        if verification is None:
            return None
        if (
            verification.user_id != evidence.user_id
            or verification.session_id != evidence.session_id
            or verification.segment_id not in (None, evidence.segment_id)
        ):
            return None
        return verification.anchor_type

    def submit_segment(self, evidence: SegmentEvidence) -> UpdateCandidate:
        config = self.settings.update_manager
        anchor = self._verification_anchor(evidence)
        g1 = not evidence.enforcement_triggered and all(
            level in (RiskLevel.LOW, RiskLevel.MEDIUM) for level in evidence.risk_levels
        )
        g2 = anchor is not None
        g3 = evidence.scored_windows >= config.min_promotable_windows
        g4 = not evidence.unexplained_gap
        candidate = UpdateCandidate(
            schema_version=PROTOCOL_VERSION,
            candidate_id=self._candidate_id_factory(),
            user_id=evidence.user_id,
            segment_id=evidence.segment_id,
            gate_evidence=GateEvidence(
                g1_risk=g1,
                g2_verification=g2,
                g3_volume=g3,
                g4_continuity=g4,
                g5_quarantine=False,
                g6_schedule=False,
            ),
            verification_anchor=anchor,
            quarantined_at=_iso(evidence.completed_at),
            incident_recorded=False,
            disposition=(
                CandidateDisposition.QUARANTINED
                if all((g1, g2, g3, g4))
                else CandidateDisposition.REJECTED
            ),
            reason_code=("QUARANTINE_STARTED" if all((g1, g2, g3, g4)) else "GATE_REJECTED"),
            audit_revision=1,
        )
        self.repository.save_candidate(candidate)
        return candidate

    def reassess(
        self,
        candidate: UpdateCandidate,
        *,
        now: datetime,
        scheduled_run: bool,
    ) -> UpdateCandidate:
        if candidate.disposition in {
            CandidateDisposition.REJECTED,
            CandidateDisposition.INVALIDATED,
            CandidateDisposition.PROMOTED,
        }:
            return candidate
        aged_at = datetime.fromisoformat(candidate.quarantined_at.replace("Z", "+00:00"))
        quarantine = timedelta(days=self.settings.update_manager.quarantine_days)
        g5 = now.astimezone(UTC) >= aged_at.astimezone(UTC) + quarantine
        evidence = candidate.gate_evidence.model_copy(
            update={"g5_quarantine": g5, "g6_schedule": scheduled_run}
        )
        fundamental = all(
            (evidence.g1_risk, evidence.g2_verification, evidence.g3_volume, evidence.g4_continuity)
        )
        eligible = fundamental and g5 and scheduled_run and not candidate.incident_recorded
        updated = candidate.model_copy(
            update={
                "gate_evidence": evidence,
                "disposition": (
                    CandidateDisposition.ELIGIBLE if eligible else CandidateDisposition.QUARANTINED
                ),
                "reason_code": "ALL_GATES_PASSED" if eligible else "QUARANTINE_OR_SCHEDULE_PENDING",
                "audit_revision": candidate.audit_revision + 1,
            }
        )
        self.repository.save_candidate(updated)
        return updated

    def invalidate(self, candidate: UpdateCandidate, *, reason_code: str) -> UpdateCandidate:
        if not reason_code.strip():
            raise ValueError("candidate invalidation reason must not be blank")
        updated = candidate.model_copy(
            update={
                "incident_recorded": True,
                "disposition": CandidateDisposition.INVALIDATED,
                "reason_code": reason_code,
                "audit_revision": candidate.audit_revision + 1,
            }
        )
        self.repository.save_candidate(updated)
        return updated

    def _validate(self, build: CandidateBuild) -> ValidationReport:
        tolerance = self.settings.update_manager.regression_tolerance
        baseline_far = build.baseline_metrics.false_acceptance_rate
        candidate_far = build.candidate_metrics.false_acceptance_rate
        if baseline_far is None or candidate_far is None:
            # An update may only replace an active profile when it is shown not
            # to have raised FAR. With FAR unmeasured on either side that
            # showing is impossible, so the honest answer is refusal -- never a
            # substituted zero (PLAN.md P7: adaptation must be earned).
            #
            # Enrollment is deliberately asymmetric with this: a FIRST profile
            # may activate with FAR unmeasured, because there is no prior
            # profile to regress against and its acceptance criterion is the
            # ADR-013 enrollment gate instead. See ADR-014.
            return ValidationReport(
                accepted=False,
                code="VALIDATION_FAR_UNMEASURED",
                baseline=build.baseline_metrics,
                candidate=build.candidate_metrics,
                operating_point=(
                    "far unmeasured on at least one side; regression undecidable"
                ),
            )
        frr_regression = (
            build.candidate_metrics.false_rejection_rate
            - build.baseline_metrics.false_rejection_rate
        )
        far_regression = candidate_far - baseline_far
        accepted = frr_regression <= tolerance and far_regression <= tolerance
        return ValidationReport(
            accepted=accepted,
            code="VALIDATION_PASSED" if accepted else "VALIDATION_REGRESSION",
            baseline=build.baseline_metrics,
            candidate=build.candidate_metrics,
            operating_point="pooled-EER threshold on VALIDATION with cross-user impostors",
        )

    def run_scheduled(
        self,
        *,
        user_id: str,
        scheduled_for: datetime,
        candidate_builder: CandidateBuilder,
    ) -> UpdateRunOutcome:
        if scheduled_for.tzinfo is None:
            raise ValueError("scheduled update time must be timezone-aware")
        cadence = timedelta(days=self.settings.update_manager.retraining_cadence_days)
        if not self.repository.schedule_due(user_id, scheduled_for, cadence):
            return UpdateRunOutcome("", "SKIPPED", "FIXED_CADENCE_NOT_DUE", None)

        run_id = self._run_id_factory()
        self.repository.begin_run(run_id, user_id, scheduled_for)
        try:
            assessed = tuple(
                self.reassess(candidate, now=scheduled_for, scheduled_run=True)
                for candidate in self.repository.candidates_for_user(user_id)
            )
            eligible = tuple(
                candidate
                for candidate in assessed
                if candidate.disposition
                in {CandidateDisposition.ELIGIBLE, CandidateDisposition.PROMOTED}
            )
            if not eligible:
                report: dict[str, object] = {"code": "NO_ELIGIBLE_CANDIDATES"}
                self.repository.finish_run(run_id, "REJECTED", report)
                return UpdateRunOutcome(run_id, "REJECTED", "NO_ELIGIBLE_CANDIDATES", None)

            promoted = tuple(
                (
                    candidate
                    if candidate.disposition is CandidateDisposition.PROMOTED
                    else candidate.model_copy(
                        update={
                            "disposition": CandidateDisposition.PROMOTED,
                            "reason_code": "PROMOTED_FOR_SCHEDULED_RETRAINING",
                            "audit_revision": candidate.audit_revision + 1,
                        }
                    )
                )
                for candidate in eligible
            )
            for candidate in promoted:
                if candidate.disposition is not CandidateDisposition.PROMOTED:
                    raise AssertionError("scheduled training received an unpromoted candidate")
                self.repository.save_candidate(candidate)

            build = candidate_builder(user_id, promoted)
            validation = self._validate(build)
            if not validation.accepted:
                for candidate in promoted:
                    self.repository.save_candidate(
                        candidate.model_copy(
                            update={
                                "disposition": CandidateDisposition.REJECTED,
                                "reason_code": "MODEL_VALIDATION_REGRESSION",
                                "audit_revision": candidate.audit_revision + 1,
                            }
                        )
                    )
                report = {"code": validation.code, "validation": validation.__dict__}
                self.repository.finish_run(run_id, "REJECTED", report)
                return UpdateRunOutcome(run_id, "REJECTED", validation.code, None)

            profile = ModelProfile(
                profile_version=build.profile_version,
                user_id=user_id,
                keyboard_artifact_version=build.keyboard_artifact_version,
                mouse_artifact_version=build.mouse_artifact_version,
                aggregate_checksum=build.aggregate_checksum,
                validation=validation,
                created_at=scheduled_for,
            )
            self.repository.activate_profile(
                profile,
                self.settings.update_manager.retained_model_versions,
            )
            self.repository.finish_run(
                run_id,
                "ACTIVATED",
                {"code": "PROFILE_ACTIVATED", "profile_version": profile.profile_version},
            )
            return UpdateRunOutcome(run_id, "ACTIVATED", "PROFILE_ACTIVATED", profile)
        except Exception as exc:
            self.repository.finish_run(
                run_id,
                "FAILED",
                {"code": "UPDATE_JOB_FAILED", "error_type": type(exc).__name__},
            )
            return UpdateRunOutcome(run_id, "FAILED", "UPDATE_JOB_FAILED", None)

    def rollback(self, user_id: str, *, occurred_at: datetime | None = None) -> ModelProfile:
        return self.repository.rollback(user_id, occurred_at or datetime.now(UTC))
