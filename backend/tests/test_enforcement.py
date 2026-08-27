from __future__ import annotations

from datetime import UTC, datetime

from backend.app.decisions import (
    ActionOutcome,
    ActionStatus,
    CallbackActionAdapter,
    EnforcementCoordinator,
    VerificationRecord,
)
from backend.app.decisions.adapters import AdapterResult
from protocol.generated.python.contracts import (
    DecisionAction,
    RiskDecision,
    RiskLevel,
    UserState,
    VerificationAnchor,
)


class MemoryStore:
    def __init__(self) -> None:
        self.outcomes: list[ActionOutcome] = []
        self.verifications: list[VerificationRecord] = []

    def record_action_outcome(self, outcome: ActionOutcome) -> None:
        self.outcomes.append(outcome)

    def record_verification(self, verification: VerificationRecord) -> None:
        self.verifications.append(verification)


def _decision(*, state: UserState = UserState.ACTIVE, applied: bool = True) -> RiskDecision:
    return RiskDecision.model_validate(
        {
            "schema_version": "1.0.0",
            "decision_id": "decision-1",
            "user_id": "synthetic-user",
            "session_id": "session-1",
            "segment_id": "segment-1",
            "window_id": "window-1",
            "t_decision_us": 100,
            "quality_label": "FULL",
            "fused_score": 0.9,
            "context_confidence": 1.0,
            "confidence_source": "NEUTRAL_FALLBACK",
            "smoothed_score": 0.9,
            "risk_level": "HIGH",
            "user_state": state,
            "action": "REAUTH",
            "reason_code": "SYNTHETIC_TEST",
            "threshold_config_version": "test",
            "config_checksum": "a" * 64,
            "shadow_mode": state is not UserState.ACTIVE,
            "enforcement_applied": applied,
        }
    )


def test_successful_reauth_creates_only_an_a2_proof_digest() -> None:
    store = MemoryStore()
    coordinator = EnforcementCoordinator(
        {
            DecisionAction.REAUTH: CallbackActionAdapter(
                DecisionAction.REAUTH,
                lambda request: AdapterResult(
                    ActionStatus.SUCCEEDED,
                    "REAUTH_SUCCEEDED",
                    f"proof-for-{request.correlation_id}",
                ),
            )
        },
        store=store,
        anchor_id_factory=lambda: "anchor-1",
    )
    outcome = coordinator.execute(_decision())
    assert outcome.status is ActionStatus.SUCCEEDED
    assert outcome.verification is not None
    assert outcome.verification.anchor_type is VerificationAnchor.A2_REAUTH
    assert outcome.verification.evidence_reference != "proof-for-decision-1"
    assert len(outcome.verification.evidence_reference) == 64
    assert store.verifications == [outcome.verification]


def test_non_active_and_adapter_failure_are_fail_open() -> None:
    calls = 0

    def callback(request: object) -> AdapterResult:
        nonlocal calls
        calls += 1
        raise RuntimeError("synthetic adapter failure")

    notices = []
    coordinator = EnforcementCoordinator(
        {DecisionAction.REAUTH: CallbackActionAdapter(DecisionAction.REAUTH, callback)},
        notice_sink=notices.append,
    )
    skipped = coordinator.execute(_decision(state=UserState.CALIBRATING, applied=False))
    assert skipped.status is ActionStatus.SKIPPED
    assert calls == 0
    failed = coordinator.execute(_decision())
    assert failed.status is ActionStatus.FAILED_OPEN
    assert calls == 1
    assert notices[0].severity == "HIGH"


def test_scheduled_anchor_requires_explicit_successful_evidence() -> None:
    coordinator = EnforcementCoordinator({}, anchor_id_factory=lambda: "anchor-3")
    assert (
        coordinator.record_scheduled_verification(
            user_id="synthetic-user",
            session_id="session-1",
            segment_id="segment-1",
            result=AdapterResult(ActionStatus.CANCELLED, "CANCELLED"),
        )
        is None
    )
    anchor = coordinator.record_scheduled_verification(
        user_id="synthetic-user",
        session_id="session-1",
        segment_id="segment-1",
        result=AdapterResult(ActionStatus.SUCCEEDED, "VERIFIED", "synthetic-proof"),
        authenticated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert anchor is not None
    assert anchor.anchor_type is VerificationAnchor.A3_SCHEDULED_PROMPT


def test_active_continue_adapter_records_a_successful_noop() -> None:
    decision = _decision(applied=False).model_copy(
        update={"action": DecisionAction.CONTINUE, "risk_level": RiskLevel.LOW}
    )
    outcome = EnforcementCoordinator({}).execute(decision)
    assert outcome.status is ActionStatus.SUCCEEDED
    assert outcome.code == "CONTINUE_OBSERVED"
