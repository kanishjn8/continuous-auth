from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.app.decisions import (
    ActionOutcome,
    ActionStatus,
    ChallengeNotConfigured,
    ChallengeService,
    EnforcementCoordinator,
    InvalidChallengeSetup,
    NativeChallengeAdapter,
    ResponseOutcome,
    UnknownChallenge,
    VerificationRecord,
    WindowsLockAdapter,
    load_enforcement_settings,
)
from backend.app.decisions.adapters import ActionRequest
from backend.app.decisions.challenge import ChallengeAlreadyConfigured
from backend.app.decisions.config import EnforcementSettings
from protocol.generated.python.contracts import (
    DecisionAction,
    RiskDecision,
    UserState,
    VerificationAnchor,
)

QUESTION = "What was the name of your first pet?"
ANSWER = "wellington-the-third"


class MemoryChallengeStorage:
    """Stand-in for the storage_metadata-backed document, counting reads."""

    def __init__(self) -> None:
        self.document: str | None = None
        self.reads = 0
        self.writes = 0

    def security_challenge_document(self) -> str | None:
        self.reads += 1
        return self.document

    def set_security_challenge_document(self, document: str) -> None:
        self.writes += 1
        self.document = document


class MemoryStore:
    def __init__(self) -> None:
        self.outcomes: list[ActionOutcome] = []
        self.verifications: list[VerificationRecord] = []

    def record_action_outcome(self, outcome: ActionOutcome) -> None:
        self.outcomes.append(outcome)

    def record_verification(self, verification: VerificationRecord) -> None:
        self.verifications.append(verification)


def _settings(**overrides: object) -> EnforcementSettings:
    base = {
        "config_version": "test",
        "enabled": True,
        "native_prompt": True,
        "lock_workstation": True,
        "challenge_timeout_seconds": 120.0,
        "answer_hash_iterations": 100000,
    }
    base.update(overrides)
    return EnforcementSettings(**base)  # type: ignore[arg-type]


def _service(
    storage: MemoryChallengeStorage | None = None,
    *,
    settings: EnforcementSettings | None = None,
    clock: object | None = None,
) -> tuple[ChallengeService, MemoryChallengeStorage]:
    store = storage or MemoryChallengeStorage()
    service = ChallengeService(
        store,  # type: ignore[arg-type]
        settings or _settings(),
        clock=clock,  # type: ignore[arg-type]
    )
    return service, store


def _configured() -> tuple[ChallengeService, MemoryChallengeStorage]:
    service, store = _service()
    service.configure(question=QUESTION, answer=ANSWER, confirm_answer=ANSWER)
    return service, store


@pytest.fixture
def unconfigured_service() -> ChallengeService:
    service, _ = _service()
    return service


@pytest.fixture
def configured_service() -> ChallengeService:
    service, _ = _configured()
    return service


def _decision(
    action: DecisionAction = DecisionAction.REAUTH,
    *,
    decision_id: str = "decision-1",
) -> RiskDecision:
    return RiskDecision.model_validate(
        {
            "schema_version": "1.0.0",
            "decision_id": decision_id,
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
            "user_state": UserState.ACTIVE,
            "action": action,
            "reason_code": "SYNTHETIC_TEST",
            "threshold_config_version": "test",
            "config_checksum": "a" * 64,
            "shadow_mode": False,
            "enforcement_applied": True,
        }
    )


def test_first_run_is_unconfigured_and_cannot_dispatch_a_challenge() -> None:
    service, _ = _service()
    assert service.is_configured() is False
    assert service.question() is None
    with pytest.raises(ChallengeNotConfigured):
        service.open(_decision())


def test_setup_persists_and_is_reused_on_a_later_run() -> None:
    _, store = _configured()
    reloaded, _ = _service(store)
    assert reloaded.is_configured() is True
    assert reloaded.question() == QUESTION
    pending = reloaded.open(_decision())
    assert pending.question == QUESTION


def test_stored_document_never_contains_the_plaintext_answer() -> None:
    _, store = _configured()
    assert store.document is not None
    assert ANSWER not in store.document
    assert QUESTION in store.document


def test_setup_runs_once_and_rotation_requires_the_current_answer() -> None:
    service, _ = _configured()
    with pytest.raises(ChallengeAlreadyConfigured):
        service.configure(question="New question?", answer="new-answer", confirm_answer="new-answer")
    with pytest.raises(InvalidChallengeSetup):
        service.configure(
            question="New question?",
            answer="new-answer",
            confirm_answer="new-answer",
            current_answer="not-the-answer",
        )
    service.configure(
        question="New question?",
        answer="new-answer",
        confirm_answer="new-answer",
        current_answer=ANSWER,
    )
    assert service.question() == "New question?"


def test_setup_rejects_mismatched_confirmation_and_short_values() -> None:
    service, _ = _service()
    with pytest.raises(InvalidChallengeSetup):
        service.configure(question=QUESTION, answer=ANSWER, confirm_answer="different")
    with pytest.raises(InvalidChallengeSetup):
        service.configure(question="hi", answer=ANSWER, confirm_answer=ANSWER)
    with pytest.raises(InvalidChallengeSetup):
        service.configure(question=QUESTION, answer="ab", confirm_answer="ab")
    assert service.is_configured() is False


def test_one_saved_challenge_serves_soft_challenge_and_reauth() -> None:
    service, _ = _configured()
    soft = service.open(_decision(DecisionAction.SOFT_CHALLENGE, decision_id="decision-soft"))
    reauth = service.open(_decision(DecisionAction.REAUTH, decision_id="decision-reauth"))
    assert soft.question == reauth.question == QUESTION
    assert soft.blocking is False
    assert reauth.blocking is True


def test_dispatch_touches_no_storage_and_so_cannot_block_ingestion() -> None:
    service, store = _configured()
    reads_before, writes_before = store.reads, store.writes
    for index in range(50):
        service.open(_decision(decision_id=f"decision-{index}"))
    assert store.reads == reads_before
    assert store.writes == writes_before


def test_correct_answer_is_accepted_and_creates_the_a2_anchor() -> None:
    service, _ = _configured()
    pending = service.open(_decision())
    result = service.respond(pending.decision_id, ANSWER)
    assert result.outcome is ResponseOutcome.ACCEPTED
    assert result.evidence_reference is not None

    store = MemoryStore()
    coordinator = EnforcementCoordinator({}, store=store, anchor_id_factory=lambda: "anchor-1")
    outcome = coordinator.record_challenge_response(
        decision_id=pending.decision_id,
        requested_action=pending.action,
        user_id=pending.user_id,
        session_id=pending.session_id,
        segment_id=pending.segment_id,
        accepted=True,
        code="CHALLENGE_ACCEPTED",
        evidence_reference=result.evidence_reference,
    )
    assert outcome.status is ActionStatus.SUCCEEDED
    assert outcome.verification is not None
    assert outcome.verification.anchor_type is VerificationAnchor.A2_REAUTH
    assert outcome.verification.evidence_reference != result.evidence_reference
    assert store.verifications == [outcome.verification]


def test_wrong_answer_is_rejected_and_creates_no_anchor() -> None:
    service, _ = _configured()
    pending = service.open(_decision())
    result = service.respond(pending.decision_id, "not-the-answer")
    assert result.outcome is ResponseOutcome.REJECTED
    assert result.evidence_reference is None

    store = MemoryStore()
    coordinator = EnforcementCoordinator({}, store=store, anchor_id_factory=lambda: "anchor-2")
    outcome = coordinator.record_challenge_response(
        decision_id=pending.decision_id,
        requested_action=pending.action,
        user_id=pending.user_id,
        session_id=pending.session_id,
        segment_id=pending.segment_id,
        accepted=False,
        code="CHALLENGE_REJECTED",
    )
    assert outcome.status is ActionStatus.CANCELLED
    assert outcome.verification is None
    assert store.verifications == []


def test_one_attempt_per_dispatched_challenge() -> None:
    service, _ = _configured()
    pending = service.open(_decision())
    service.respond(pending.decision_id, "not-the-answer")
    with pytest.raises(UnknownChallenge):
        service.respond(pending.decision_id, ANSWER)


def test_unknown_decision_id_is_rejected() -> None:
    service, _ = _configured()
    with pytest.raises(UnknownChallenge):
        service.respond("decision-that-was-never-dispatched", ANSWER)


def test_a_wrong_response_token_is_rejected_as_unknown() -> None:
    service, _ = _configured()
    pending = service.open(_decision())
    with pytest.raises(UnknownChallenge):
        service.respond(pending.decision_id, ANSWER, response_token="forged-token")
    accepted = service.respond(pending.decision_id, ANSWER, response_token=pending.response_token)
    assert accepted.outcome is ResponseOutcome.ACCEPTED


def test_an_unanswered_challenge_expires() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    moment = {"value": now}
    service, _ = _service(clock=lambda: moment["value"])
    service.configure(question=QUESTION, answer=ANSWER, confirm_answer=ANSWER)
    pending = service.open(_decision())
    moment["value"] = now + timedelta(seconds=121)
    assert service.respond(pending.decision_id, ANSWER).outcome is ResponseOutcome.EXPIRED
    assert service.pending() == ()


def test_enforcement_is_disabled_in_the_committed_configuration() -> None:
    settings = load_enforcement_settings()
    assert settings.enabled is False
    assert settings.prompt_enabled is False
    assert settings.workstation_lock_enabled is False


def test_challenge_is_registered_but_no_prompt_runs_while_enforcement_is_off() -> None:
    service, _ = _configured()
    adapter = NativeChallengeAdapter(
        DecisionAction.REAUTH,
        service=service,
        settings=_settings(enabled=False),
        response_endpoint="http://127.0.0.1:8765/v1/enforcement/challenge",
    )
    decision = _decision()
    result = adapter.execute(ActionRequest(decision, decision.decision_id))
    assert result.status is ActionStatus.SKIPPED
    assert result.code == "ENFORCEMENT_DISABLED"
    assert service.pending()[0].decision_id == decision.decision_id


def test_challenge_dispatch_is_skipped_before_setup() -> None:
    service, _ = _service()
    adapter = NativeChallengeAdapter(
        DecisionAction.SOFT_CHALLENGE,
        service=service,
        settings=_settings(),
        response_endpoint="http://127.0.0.1:8765/v1/enforcement/challenge",
    )
    decision = _decision(DecisionAction.SOFT_CHALLENGE)
    result = adapter.execute(ActionRequest(decision, decision.decision_id))
    assert result.status is ActionStatus.SKIPPED
    assert result.code == "CHALLENGE_NOT_CONFIGURED"


def test_terminate_never_locks_while_enforcement_is_off() -> None:
    locked: list[bool] = []
    adapter = WindowsLockAdapter(_settings(enabled=False))
    decision = _decision(DecisionAction.TERMINATE)
    result = adapter.execute(ActionRequest(decision, decision.decision_id))
    assert result.status is ActionStatus.SKIPPED
    assert result.code == "ENFORCEMENT_DISABLED"
    assert locked == []


def test_terminate_locks_the_workstation_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("backend.app.decisions.native.windows_platform", lambda: True)
    monkeypatch.setattr(
        "backend.app.decisions.native.lock_workstation",
        lambda: (calls.append("locked"), True)[1],
    )
    adapter = WindowsLockAdapter(_settings())
    decision = _decision(DecisionAction.TERMINATE)
    result = adapter.execute(ActionRequest(decision, decision.decision_id))
    assert result.status is ActionStatus.SUCCEEDED
    assert result.code == "WORKSTATION_LOCKED"
    assert calls == ["locked"]


def test_no_windows_enforcement_is_attempted_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.app.decisions.native.windows_platform", lambda: False)
    monkeypatch.setattr(
        "backend.app.decisions.native.lock_workstation",
        lambda: pytest.fail("lock_workstation must not run off Windows"),
    )
    decision = _decision(DecisionAction.TERMINATE)
    lock = WindowsLockAdapter(_settings()).execute(ActionRequest(decision, decision.decision_id))
    assert lock.status is ActionStatus.SKIPPED
    assert lock.code == "WORKSTATION_LOCK_UNSUPPORTED_PLATFORM"

    service, _ = _configured()
    monkeypatch.setattr(
        "backend.app.decisions.native.spawn_prompt",
        lambda dispatch: pytest.fail("no native prompt may spawn off Windows"),
    )
    adapter = NativeChallengeAdapter(
        DecisionAction.REAUTH,
        service=service,
        settings=_settings(),
        response_endpoint="http://127.0.0.1:8765/v1/enforcement/challenge",
    )
    reauth = _decision()
    prompt = adapter.execute(ActionRequest(reauth, reauth.decision_id))
    assert prompt.status is ActionStatus.SKIPPED
    assert prompt.code == "NATIVE_PROMPT_UNSUPPORTED_PLATFORM"


def test_open_scheduled_registers_a_non_blocking_pending_challenge(
    configured_service: ChallengeService,
) -> None:
    pending = configured_service.open_scheduled(
        user_id="participant-01", session_id="s1", segment_id="seg1"
    )
    assert pending.decision_id.startswith("scheduled-anchor:")
    assert pending.user_id == "participant-01"
    assert pending.session_id == "s1"
    assert pending.segment_id == "seg1"
    assert pending.blocking is False
    assert pending in configured_service.pending()


def test_open_scheduled_requires_a_configured_challenge(
    unconfigured_service: ChallengeService,
) -> None:
    with pytest.raises(ChallengeNotConfigured):
        unconfigured_service.open_scheduled(
            user_id="participant-01", session_id="s1", segment_id="seg1"
        )


def test_scheduled_challenges_get_distinct_ids(configured_service: ChallengeService) -> None:
    first = configured_service.open_scheduled(
        user_id="participant-01", session_id="s1", segment_id="seg1"
    )
    second = configured_service.open_scheduled(
        user_id="participant-01", session_id="s1", segment_id="seg2"
    )
    assert first.decision_id != second.decision_id


def test_a_correct_answer_to_a_scheduled_challenge_yields_evidence(
    configured_service: ChallengeService,
) -> None:
    pending = configured_service.open_scheduled(
        user_id="participant-01", session_id="s1", segment_id="seg1"
    )
    result = configured_service.respond(pending.decision_id, ANSWER)
    assert result.outcome is ResponseOutcome.ACCEPTED
    assert result.evidence_reference is not None
