"""The escalation ladder as a session state, and the API gate that enforces it.

Covers the half of PLAN.md Section 11.3 that the native adapters do not: what
the *backend* does once a challenge has been dispatched, answered, failed, or
ignored. The OS-level half (native prompt, workstation lock) is covered by
``backend/tests/test_challenge.py`` and is unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.api.backend import SQLiteApiBackend
from backend.app.api.routes import create_api_app
from backend.app.decisions.challenge import (
    ChallengeService,
    PendingChallenge,
    ResponseOutcome,
)
from backend.app.decisions.config import EnforcementSettings
from backend.app.decisions.session import EnforcementPosture, EnforcementSessionState
from backend.app.storage.audit import AuditLog
from backend.app.storage.config import StorageSettings
from backend.app.storage.database import SQLiteDatabase
from backend.app.storage.service import StorageService
from protocol.generated.python.contracts import (
    DecisionAction,
    RetentionConfig,
    StorageDataPolicy,
    StorageEnvironment,
)

SECRET = "synthetic-enforcement-secret"
QUESTION = "What was the name of your first pet?"
ANSWER = "wellington-the-third"
WORKSPACE = Path(__file__).resolve().parents[2]


def _settings(*, timeout: float = 120.0) -> EnforcementSettings:
    return EnforcementSettings(
        config_version="enforcement-session-test",
        # Enforcement on: these tests are about what the enabled system does.
        # The committed default stays false and is asserted elsewhere.
        enabled=True,
        native_prompt=False,
        lock_workstation=False,
        challenge_timeout_seconds=timeout,
        answer_hash_iterations=100_000,
    )


def _storage(tmp_path: Path) -> StorageService:
    root = tmp_path / "store"
    root.mkdir()
    audit_directory = root / "audit"
    audit_directory.mkdir()
    settings = StorageSettings(
        config_version="enforcement-session-test",
        protocol_version="1.0.0",
        environment=StorageEnvironment.DEVELOPMENT,
        data_policy=StorageDataPolicy.SYNTHETIC_ONLY,
        root_directory=root,
        database_path=root / "runtime.db",
        audit_directory=audit_directory,
        busy_timeout_ms=5000,
        retention=RetentionConfig(
            feature_window_days=7,
            score_days=7,
            audit_compress_after_days=1,
            audit_delete_after_days=7,
            raw_debug_capture_enabled=False,
            pilot_mode=False,
        ),
    )
    database = SQLiteDatabase(settings.database_path, settings.busy_timeout_ms)
    database.initialise()
    return StorageService(settings, database, AuditLog(audit_directory), None)


# ---------------------------------------------------------------------------
# The state machine itself
# ---------------------------------------------------------------------------


def _state(*, enabled: bool = True) -> tuple[EnforcementSessionState, list[str]]:
    recorded: list[str] = []
    state = EnforcementSessionState(
        enabled=enabled,
        transition_sink=lambda transition: recorded.append(transition.code),
    )
    return state, recorded


def _applied(state: EnforcementSessionState, action: DecisionAction) -> None:
    state.observe_decision(
        decision_id=f"decision-{action.value}", action=action, enforcement_applied=True
    )


def test_low_risk_leaves_the_session_normal() -> None:
    state, recorded = _state()
    state.observe_decision(
        decision_id="decision-1", action=DecisionAction.CONTINUE, enforcement_applied=True
    )
    assert state.posture is EnforcementPosture.NORMAL
    assert not state.blocks_protected_access()
    assert recorded == []


def test_medium_raises_a_soft_challenge_that_does_not_block() -> None:
    state, recorded = _state()
    _applied(state, DecisionAction.SOFT_CHALLENGE)
    assert state.posture is EnforcementPosture.SOFT_CHALLENGE
    # "Soft" means work continues; only failing it costs access.
    assert not state.blocks_protected_access()
    assert recorded == ["ENFORCEMENT_SOFT_CHALLENGE"]


def test_a_satisfied_soft_challenge_returns_to_normal() -> None:
    state, _ = _state()
    _applied(state, DecisionAction.SOFT_CHALLENGE)
    state.observe_challenge_outcome(
        decision_id="decision-SOFT_CHALLENGE",
        action=DecisionAction.SOFT_CHALLENGE,
        outcome=ResponseOutcome.ACCEPTED,
    )
    assert state.posture is EnforcementPosture.NORMAL


@pytest.mark.parametrize("outcome", [ResponseOutcome.REJECTED, ResponseOutcome.EXPIRED])
def test_a_failed_soft_challenge_climbs_one_rung(outcome: ResponseOutcome) -> None:
    """The ladder's next step is REAUTH, not lockout."""

    state, _ = _state()
    _applied(state, DecisionAction.SOFT_CHALLENGE)
    state.observe_challenge_outcome(
        decision_id="decision-SOFT_CHALLENGE",
        action=DecisionAction.SOFT_CHALLENGE,
        outcome=outcome,
    )
    assert state.posture is EnforcementPosture.REAUTH_REQUIRED
    assert state.blocks_protected_access()


def test_high_requires_reauthentication_and_blocks() -> None:
    state, recorded = _state()
    _applied(state, DecisionAction.REAUTH)
    assert state.posture is EnforcementPosture.REAUTH_REQUIRED
    assert state.blocks_protected_access()
    assert recorded == ["ENFORCEMENT_REAUTH_REQUIRED"]


def test_successful_reauthentication_restores_the_session() -> None:
    state, _ = _state()
    _applied(state, DecisionAction.REAUTH)
    state.observe_challenge_outcome(
        decision_id="decision-REAUTH",
        action=DecisionAction.REAUTH,
        outcome=ResponseOutcome.ACCEPTED,
    )
    assert state.posture is EnforcementPosture.NORMAL
    assert not state.blocks_protected_access()
    assert state.snapshot()["failed_responses"] == 0


@pytest.mark.parametrize("outcome", [ResponseOutcome.REJECTED, ResponseOutcome.EXPIRED])
def test_a_failed_reauthentication_locks_the_session_out(outcome: ResponseOutcome) -> None:
    """PLAN.md 11.3: TERMINATE on "HIGH sustained ... or failed reauth"."""

    state, recorded = _state()
    _applied(state, DecisionAction.REAUTH)
    state.observe_challenge_outcome(
        decision_id="decision-REAUTH", action=DecisionAction.REAUTH, outcome=outcome
    )
    assert state.posture is EnforcementPosture.LOCKED_OUT
    assert state.blocks_protected_access()
    assert recorded[-1] == f"ENFORCEMENT_CHALLENGE_{outcome.value}"


def test_terminate_locks_the_session_out_directly() -> None:
    state, _ = _state()
    _applied(state, DecisionAction.TERMINATE)
    assert state.posture is EnforcementPosture.LOCKED_OUT


def test_only_a_reauthentication_clears_a_lockout() -> None:
    state, _ = _state()
    _applied(state, DecisionAction.TERMINATE)
    state.observe_challenge_outcome(
        decision_id="stale",
        action=DecisionAction.SOFT_CHALLENGE,
        outcome=ResponseOutcome.ACCEPTED,
    )
    assert state.posture is EnforcementPosture.LOCKED_OUT
    state.observe_challenge_outcome(
        decision_id="recovery:1",
        action=DecisionAction.REAUTH,
        outcome=ResponseOutcome.ACCEPTED,
    )
    assert state.posture.value == EnforcementPosture.NORMAL.value


def test_a_stale_soft_answer_cannot_unwind_an_escalation() -> None:
    """A soft challenge answered after the session escalated must not help."""

    state, _ = _state()
    _applied(state, DecisionAction.SOFT_CHALLENGE)
    _applied(state, DecisionAction.REAUTH)
    state.observe_challenge_outcome(
        decision_id="decision-SOFT_CHALLENGE",
        action=DecisionAction.SOFT_CHALLENGE,
        outcome=ResponseOutcome.ACCEPTED,
    )
    assert state.posture is EnforcementPosture.REAUTH_REQUIRED


def test_posture_never_de_escalates_on_a_quieter_decision() -> None:
    """A single LOW window must not undo an outstanding reauthentication."""

    state, _ = _state()
    _applied(state, DecisionAction.REAUTH)
    state.observe_decision(
        decision_id="decision-later", action=DecisionAction.CONTINUE, enforcement_applied=True
    )
    _applied(state, DecisionAction.SOFT_CHALLENGE)
    assert state.posture is EnforcementPosture.REAUTH_REQUIRED


def test_unenforced_decisions_change_nothing() -> None:
    """Cooldown, action budget, shadow mode and non-ACTIVE states all suppress."""

    state, _ = _state()
    state.observe_decision(
        decision_id="decision-1", action=DecisionAction.REAUTH, enforcement_applied=False
    )
    assert state.posture is EnforcementPosture.NORMAL


def test_the_committed_disabled_configuration_never_blocks() -> None:
    """Ordinary collection must not be able to lock the operator out."""

    state, recorded = _state(enabled=False)
    _applied(state, DecisionAction.TERMINATE)
    assert state.posture is EnforcementPosture.NORMAL
    assert not state.blocks_protected_access()
    assert recorded == []


# ---------------------------------------------------------------------------
# The API gate
# ---------------------------------------------------------------------------


class _Harness:
    """A C7 app wired to a real challenge service and a shared posture."""

    def __init__(self, tmp_path: Path, *, timeout: float = 120.0) -> None:
        self.moment = datetime(2026, 1, 1, tzinfo=UTC)
        self.settings = _settings(timeout=timeout)
        self.storage = _storage(tmp_path)
        self.service = ChallengeService(self.storage, self.settings, clock=lambda: self.moment)
        self.service.configure(question=QUESTION, answer=ANSWER, confirm_answer=ANSWER)
        self.state = EnforcementSessionState(enabled=True, clock=lambda: self.moment)
        self.opened: list[PendingChallenge] = []
        self.anchors: list[dict[str, object]] = []
        self.backend = SQLiteApiBackend(
            self.storage,
            active_user_provider=lambda: None,
            challenge_service=self.service,
            enforcement_settings=self.settings,
            session_state=self.state,
            reauth_opener=self._open_reauth,
            recovery_anchor_sink=lambda **kwargs: self.anchors.append(dict(kwargs)),
        )
        from backend.app.api.config import load_api_settings

        self.app = create_api_app(
            settings=load_api_settings(WORKSPACE / "config/api.development.yaml"),
            backend=self.backend,
            local_secret=SECRET,
        )

    def _open_reauth(self) -> PendingChallenge:
        pending = self.service.open_reauthentication(
            user_id="participant-01", session_id="session-1", segment_id="segment-1"
        )
        self.opened.append(pending)
        return pending

    def escalate(self, action: DecisionAction) -> PendingChallenge | None:
        """Apply an enforcing decision exactly as EnforcementCoordinator would."""

        self.state.observe_decision(
            decision_id=f"decision-{action.value}", action=action, enforcement_applied=True
        )
        if action is DecisionAction.TERMINATE:
            return None
        from protocol.generated.python.contracts import RiskDecision

        decision = RiskDecision.model_validate(
            {
                "schema_version": "1.0.0",
                "decision_id": f"decision-{action.value}",
                "user_id": "participant-01",
                "session_id": "session-1",
                "segment_id": "segment-1",
                "window_id": "window-1",
                "t_decision_us": 1,
                "quality_label": "FULL",
                "fused_score": 0.95,
                "context_confidence": 1.0,
                "confidence_source": "NEUTRAL_FALLBACK",
                "smoothed_score": 0.95,
                "risk_level": "HIGH",
                "user_state": "ACTIVE",
                "action": action.value,
                "reason_code": "RISK_ESCALATION",
                "threshold_config_version": "test",
                "config_checksum": "a" * 64,
                "shadow_mode": False,
                "enforcement_applied": True,
            }
        )
        return self.service.open(decision)


@pytest.fixture
def harness(tmp_path: Path) -> _Harness:
    return _Harness(tmp_path)


@pytest.fixture
def client(harness: _Harness) -> Iterator[TestClient]:
    with TestClient(harness.app) as test_client:
        response = test_client.post("/v1/auth/login", json={"local_secret": SECRET})
        assert response.status_code == 200
        yield test_client


PROTECTED = (
    "/v1/state",
    "/v1/profiles",
    "/v1/history/decisions",
    "/v1/alerts",
    "/v1/metrics",
    "/v1/health",
    "/v1/updates",
    "/v1/collection/provenance",
)


def test_low_risk_serves_protected_resources(client: TestClient) -> None:
    for endpoint in PROTECTED:
        assert client.get(endpoint).status_code == 200, endpoint


def test_a_soft_challenge_does_not_block_protected_resources(
    client: TestClient, harness: _Harness
) -> None:
    pending = harness.escalate(DecisionAction.SOFT_CHALLENGE)
    assert pending is not None
    assert client.get("/v1/state").status_code == 200
    status = client.get("/v1/enforcement/status").json()
    assert status["session"]["posture"] == "SOFT_CHALLENGE"
    assert status["pending_challenge"]["decision_id"] == pending.decision_id


def test_medium_surfaces_the_challenge_the_participant_must_answer(
    client: TestClient, harness: _Harness
) -> None:
    pending = harness.escalate(DecisionAction.SOFT_CHALLENGE)
    assert pending is not None
    status = client.get("/v1/enforcement/status").json()
    assert status["pending_challenge"]["question"] == QUESTION
    assert status["pending_challenge"]["blocking"] is False
    # The answer never leaves the process in any form.
    assert ANSWER not in json.dumps(status)


def test_a_correct_soft_answer_recovers_without_disabling_monitoring(
    client: TestClient, harness: _Harness
) -> None:
    pending = harness.escalate(DecisionAction.SOFT_CHALLENGE)
    assert pending is not None
    response = client.post(
        f"/v1/enforcement/challenge/{pending.decision_id}/respond", json={"answer": ANSWER}
    )
    assert response.json()["outcome"] == "ACCEPTED"
    assert harness.state.posture is EnforcementPosture.NORMAL
    # The engine is untouched: a later escalation still escalates.
    harness.escalate(DecisionAction.REAUTH)
    assert harness.state.posture.value == EnforcementPosture.REAUTH_REQUIRED.value


def test_a_wrong_soft_answer_escalates_to_reauthentication(
    client: TestClient, harness: _Harness
) -> None:
    pending = harness.escalate(DecisionAction.SOFT_CHALLENGE)
    assert pending is not None
    response = client.post(
        f"/v1/enforcement/challenge/{pending.decision_id}/respond", json={"answer": "wrong-answer"}
    )
    assert response.json()["outcome"] == "REJECTED"
    assert client.get("/v1/state").status_code == 403


def test_high_blocks_every_protected_route(client: TestClient, harness: _Harness) -> None:
    harness.escalate(DecisionAction.REAUTH)
    for endpoint in PROTECTED:
        response = client.get(endpoint)
        assert response.status_code == 403, endpoint
        assert response.json()["code"] == "REAUTHENTICATION_REQUIRED"


def test_a_blocked_session_can_still_read_what_it_must_do(
    client: TestClient, harness: _Harness
) -> None:
    harness.escalate(DecisionAction.REAUTH)
    status = client.get("/v1/enforcement/status")
    assert status.status_code == 200
    assert status.json()["session"]["blocks_protected_access"] is True
    assert client.get("/v1/enforcement/challenge").status_code == 200


def test_valid_reauthentication_restores_protected_access(
    client: TestClient, harness: _Harness
) -> None:
    pending = harness.escalate(DecisionAction.REAUTH)
    assert pending is not None
    assert client.get("/v1/state").status_code == 403
    response = client.post(
        f"/v1/enforcement/challenge/{pending.decision_id}/respond", json={"answer": ANSWER}
    )
    assert response.json()["outcome"] == "ACCEPTED"
    assert client.get("/v1/state").status_code == 200


def test_invalid_reauthentication_locks_out_rather_than_restoring(
    client: TestClient, harness: _Harness
) -> None:
    pending = harness.escalate(DecisionAction.REAUTH)
    assert pending is not None
    response = client.post(
        f"/v1/enforcement/challenge/{pending.decision_id}/respond", json={"answer": "not-it"}
    )
    assert response.json()["outcome"] == "REJECTED"
    blocked = client.get("/v1/state")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "SESSION_LOCKED_OUT"


def test_an_ignored_reauthentication_times_out_into_lockout(tmp_path: Path) -> None:
    """Walking away from a forced reauthentication is a failed reauthentication.

    config/enforcement.development.yaml states exactly this. Before the
    expiry sweep existed, an unanswered challenge was silently discarded and
    the session carried on -- the cheapest possible bypass.
    """

    harness = _Harness(tmp_path, timeout=60.0)
    with TestClient(harness.app) as client:
        client.post("/v1/auth/login", json={"local_secret": SECRET})
        harness.escalate(DecisionAction.REAUTH)
        harness.moment = harness.moment + timedelta(seconds=61)
        blocked = client.get("/v1/state")
        assert blocked.status_code == 403
        assert blocked.json()["code"] == "SESSION_LOCKED_OUT"


def test_a_reload_during_reauthentication_does_not_bypass_it(
    client: TestClient, harness: _Harness
) -> None:
    """A refresh is just another request; the posture lives in the backend."""

    harness.escalate(DecisionAction.REAUTH)
    for _ in range(3):
        assert client.get("/v1/state").status_code == 403
    assert client.get("/v1/enforcement/status").json()["session"]["posture"] == "REAUTH_REQUIRED"


def test_a_fresh_login_does_not_clear_a_lockout(client: TestClient, harness: _Harness) -> None:
    """Enforcement belongs to the runtime session, not the browser cookie."""

    harness.escalate(DecisionAction.TERMINATE)
    client.post("/v1/auth/logout")
    assert client.post("/v1/auth/login", json={"local_secret": SECRET}).status_code == 200
    assert client.get("/v1/state").status_code == 403


def test_a_direct_api_request_during_lockout_is_rejected(
    client: TestClient, harness: _Harness
) -> None:
    """Not only reads: the administrative surface is gated too."""

    harness.escalate(DecisionAction.TERMINATE)
    assert client.put("/v1/admin/shadow-mode", json={"enabled": True}).status_code == 403
    assert client.post("/v1/admin/models/participant-01/rollback").status_code == 403
    assert client.post("/v1/alerts/alert-1/acknowledge").status_code == 403


def test_the_challenge_cannot_be_rotated_from_a_blocked_session(
    client: TestClient, harness: _Harness
) -> None:
    """Otherwise an occupant would simply replace the credential and walk on."""

    harness.escalate(DecisionAction.REAUTH)
    response = client.put(
        "/v1/enforcement/challenge",
        json={
            "question": "a new question",
            "answer": "a-new-answer",
            "confirm_answer": "a-new-answer",
            "current_answer": None,
        },
    )
    assert response.status_code == 403


def test_the_live_stream_is_closed_while_enforcement_blocks(
    client: TestClient, harness: _Harness
) -> None:
    """Reconnecting must not be the route around a pending reauthentication."""

    harness.escalate(DecisionAction.REAUTH)
    with (
        client.websocket_connect("/v1/stream") as websocket,
        pytest.raises(WebSocketDisconnect) as closed,
    ):
        websocket.receive_json()
    assert closed.value.code == 4403


def test_recovery_reauthentication_clears_a_lockout_and_anchors_it(
    client: TestClient, harness: _Harness
) -> None:
    """The dispatched challenge is gone after a lockout; recovery issues one."""

    harness.escalate(DecisionAction.REAUTH)
    first = harness.service.active_challenge()
    assert first is not None
    client.post(f"/v1/enforcement/challenge/{first.decision_id}/respond", json={"answer": "wrong"})
    assert harness.state.posture is EnforcementPosture.LOCKED_OUT

    issued = client.post("/v1/enforcement/reauthenticate")
    assert issued.status_code == 200
    decision_id = issued.json()["decision_id"]
    assert decision_id.startswith("recovery:")
    assert issued.json()["question"] == QUESTION

    response = client.post(
        f"/v1/enforcement/challenge/{decision_id}/respond", json={"answer": ANSWER}
    )
    assert response.json()["outcome"] == "ACCEPTED"
    assert client.get("/v1/state").status_code == 200
    # A2: the segment is anchored to an explicit successful reauthentication.
    assert len(harness.anchors) == 1
    assert harness.anchors[0]["session_id"] == "session-1"


def test_a_wrong_recovery_answer_leaves_the_lockout_in_place(
    client: TestClient, harness: _Harness
) -> None:
    harness.escalate(DecisionAction.TERMINATE)
    decision_id = client.post("/v1/enforcement/reauthenticate").json()["decision_id"]
    response = client.post(
        f"/v1/enforcement/challenge/{decision_id}/respond", json={"answer": "still-wrong"}
    )
    assert response.json()["outcome"] == "REJECTED"
    assert harness.state.posture is EnforcementPosture.LOCKED_OUT
    assert client.get("/v1/state").status_code == 403
    assert harness.anchors == []


def test_reauthentication_cannot_be_minted_by_an_unblocked_session(
    client: TestClient,
) -> None:
    """Otherwise any session could manufacture promotion-gate A2 evidence."""

    assert client.post("/v1/enforcement/reauthenticate").status_code == 409


def test_recovery_reuses_an_outstanding_challenge_rather_than_cycling_it(
    client: TestClient, harness: _Harness
) -> None:
    """Minting a new one per request would reset the timeout on demand."""

    harness.escalate(DecisionAction.REAUTH)
    first = client.post("/v1/enforcement/reauthenticate").json()["decision_id"]
    second = client.post("/v1/enforcement/reauthenticate").json()["decision_id"]
    assert first == second


def test_a_scheduled_prompt_never_moves_the_enforcement_posture(
    client: TestClient, harness: _Harness
) -> None:
    """A3 is routine verification, not a risk response (PLAN.md 12.2)."""

    pending = harness.service.open_scheduled(
        user_id="participant-01", session_id="session-1", segment_id="segment-1"
    )
    client.post(
        f"/v1/enforcement/challenge/{pending.decision_id}/respond", json={"answer": "wrong"}
    )
    assert harness.state.posture is EnforcementPosture.NORMAL
    assert client.get("/v1/state").status_code == 200


# ---------------------------------------------------------------------------
# Wiring: the coordinator, the orchestrator, and the drill
# ---------------------------------------------------------------------------


def test_the_coordinator_raises_the_posture_when_it_applies_an_action() -> None:
    """The seam the ingestion thread actually goes through."""

    from backend.app.decisions.adapters import EnforcementCoordinator
    from backend.tests.test_enforcement import MemoryStore, _decision

    state, _ = _state()
    coordinator = EnforcementCoordinator({}, store=MemoryStore(), session_state=state)
    coordinator.execute(_decision())
    assert state.posture is EnforcementPosture.REAUTH_REQUIRED


def test_an_adapter_that_fails_open_still_leaves_the_posture_raised() -> None:
    """ADR-011 fail-open governs the OS action, not the backend's memory.

    Off Windows every native adapter reports SKIPPED. If the posture followed
    the adapter's status, the entire ladder would be a no-op on any machine
    that is not the participant's, including CI.
    """

    from backend.app.decisions.adapters import EnforcementCoordinator
    from backend.tests.test_enforcement import MemoryStore, _decision

    state, _ = _state()
    coordinator = EnforcementCoordinator({}, store=MemoryStore(), session_state=state)
    outcome = coordinator.execute(_decision())
    assert outcome.status.value == "FAILED_OPEN"
    assert state.blocks_protected_access()


def test_a_state_gated_decision_does_not_raise_the_posture() -> None:
    """CALIBRATING, DEGRADED and shadow mode must remain non-enforcing."""

    from backend.app.decisions.adapters import EnforcementCoordinator
    from backend.tests.test_enforcement import MemoryStore, _decision
    from protocol.generated.python.contracts import UserState

    state, _ = _state()
    coordinator = EnforcementCoordinator({}, store=MemoryStore(), session_state=state)
    coordinator.execute(_decision(state=UserState.CALIBRATING, applied=False))
    assert state.posture is EnforcementPosture.NORMAL


def test_the_orchestrator_shares_one_posture_and_audits_every_transition(
    tmp_path: Path,
) -> None:
    """The drill reads escalation from `alerts` and the audit chain."""

    from backend.tests.test_scheduled_anchors import _orchestrator as build_orchestrator

    orchestrator = build_orchestrator(tmp_path)
    # The committed configuration disables enforcement, which the shared
    # posture must honour exactly as the native adapters do.
    assert orchestrator.session_state.enabled is False
    assert orchestrator.enforcement.session_state is orchestrator.session_state

    enabled = EnforcementSessionState(
        enabled=True, transition_sink=orchestrator._on_posture_transition
    )
    enabled.observe_decision(
        decision_id="decision-1", action=DecisionAction.REAUTH, enforcement_applied=True
    )
    with orchestrator.storage.database.connection() as connection:
        rows = connection.execute(
            "SELECT code, alert_type FROM alerts ORDER BY occurred_at_utc"
        ).fetchall()
    assert [row["code"] for row in rows] == ["ENFORCEMENT_REAUTH_REQUIRED"]
    assert rows[0]["alert_type"] == "BEHAVIORAL"


def test_a_drill_session_still_escalates_and_still_stays_out_of_every_corpus(
    tmp_path: Path,
) -> None:
    """ADR-014: a drill suppresses training paths, never the security path."""

    from backend.tests.test_scheduled_anchors import _orchestrator as build_orchestrator

    orchestrator = build_orchestrator(tmp_path)
    orchestrator._drill_label = "drill-01"
    lifecycle, _ = orchestrator.start_authenticated_session(
        user_id="participant-1", evidence_reference="entry-proof"
    )
    assert lifecycle.session_id is not None
    assert orchestrator.storage.is_drill_session(lifecycle.session_id)

    state, recorded = _state()
    state.observe_decision(
        decision_id="decision-1", action=DecisionAction.REAUTH, enforcement_applied=True
    )
    assert state.blocks_protected_access()
    assert recorded == ["ENFORCEMENT_REAUTH_REQUIRED"]
