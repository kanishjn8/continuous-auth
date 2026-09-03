from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api.backend import SQLiteApiBackend
from backend.app.decisions.adapters import EnforcementCoordinator
from backend.app.decisions.challenge import ChallengeService
from backend.app.decisions.config import EnforcementSettings
from backend.app.main import create_runtime_app
from backend.app.storage.audit import AuditLog
from backend.app.storage.config import StorageSettings
from backend.app.storage.database import SQLiteDatabase
from backend.app.storage.service import StorageService
from protocol.generated.python.contracts import (
    DecisionAction,
    RetentionConfig,
    RiskDecision,
    StorageDataPolicy,
    StorageEnvironment,
    UserState,
)

SECRET = "synthetic-challenge-secret"
QUESTION = "What was the name of your first pet?"
ANSWER = "wellington-the-third"

WORKSPACE = Path(__file__).resolve().parents[2]

# -- fixtures for the scheduled-anchor sink tests below ---------------------
#
# These build SQLiteApiBackend directly (no FastAPI TestClient) so the sink
# callback can be inspected in-process. Storage construction mirrors the
# `_storage(tmp_path)` helper in backend/tests/test_scheduled_anchors.py;
# the decision payload mirrors `_decision()` in backend/tests/test_challenge.py
# -- this file has no RiskDecision-building fixture of its own to mirror,
# since its other tests all go through the HTTP TestClient.

SCHEDULED_ANSWER = "correct-horse"


class _MemoryEnforcementStore:
    """Stand-in for EnforcementCoordinator's store.

    ``EnforcementCoordinator.record_challenge_response`` runs unconditionally
    for every response (both scheduled and ordinary), and its
    ``record_action_outcome`` expects a matching row in the real ``decisions``
    table -- which these fixtures never populate, since they exercise
    ChallengeService/SQLiteApiBackend directly rather than the full ingestion
    pipeline. Mirrors ``MemoryStore`` in backend/tests/test_challenge.py so
    the enforcement coordinator has somewhere to write without that
    dependency.
    """

    def __init__(self) -> None:
        self.outcomes: list[object] = []
        self.verifications: list[object] = []

    def record_action_outcome(self, outcome: object) -> None:
        self.outcomes.append(outcome)

    def record_verification(self, verification: object) -> None:
        self.verifications.append(verification)


def _storage(tmp_path: Path) -> StorageService:
    root = tmp_path / "store"
    root.mkdir()
    audit_directory = root / "audit"
    audit_directory.mkdir()
    settings = StorageSettings(
        config_version="challenge-api-test",
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


@pytest.fixture
def api_backend_with_sink(
    tmp_path: Path,
) -> tuple[SQLiteApiBackend, ChallengeService, list[dict[str, str]]]:
    """Build SQLiteApiBackend with a recording scheduled-anchor sink."""

    storage = _storage(tmp_path)
    settings = EnforcementSettings(
        config_version="challenge-api-test",
        enabled=False,
        native_prompt=True,
        lock_workstation=False,
        challenge_timeout_seconds=120.0,
        answer_hash_iterations=100_000,
    )
    service = ChallengeService(storage, settings)
    service.configure(
        question=QUESTION, answer=SCHEDULED_ANSWER, confirm_answer=SCHEDULED_ANSWER
    )
    enforcement = EnforcementCoordinator({}, store=_MemoryEnforcementStore())

    recorded: list[dict[str, str]] = []

    def sink(**kwargs: str) -> None:
        recorded.append(kwargs)

    backend = SQLiteApiBackend(
        storage,
        active_user_provider=lambda: "participant-01",
        challenge_service=service,
        enforcement=enforcement,
        enforcement_settings=settings,
        scheduled_anchor_sink=sink,
    )
    return backend, service, recorded


@pytest.fixture
def sample_decision() -> RiskDecision:
    return RiskDecision.model_validate(
        {
            "schema_version": "1.0.0",
            "decision_id": "decision-1",
            "user_id": "participant-01",
            "session_id": "s1",
            "segment_id": "seg1",
            "window_id": "window-1",
            "t_decision_us": 100,
            "quality_label": "FULL",
            "fused_score": 0.9,
            "context_confidence": 1.0,
            "confidence_source": "NEUTRAL_FALLBACK",
            "smoothed_score": 0.9,
            "risk_level": "HIGH",
            "user_state": UserState.ACTIVE,
            "action": DecisionAction.REAUTH,
            "reason_code": "SYNTHETIC_TEST",
            "threshold_config_version": "test",
            "config_checksum": "a" * 64,
            "shadow_mode": False,
            "enforcement_applied": True,
        }
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app = create_runtime_app(
        local_secret=SECRET,
        active_user_provider=lambda: None,
        storage_config=WORKSPACE / "config/storage.development.yaml",
        workspace_root=WORKSPACE,
    )
    with TestClient(app) as test_client:
        test_client.post("/v1/auth/login", json={"local_secret": SECRET})
        yield test_client


def test_challenge_endpoints_require_authentication(client: TestClient) -> None:
    client.post("/v1/auth/logout")
    assert client.get("/v1/enforcement/challenge").status_code == 401
    assert (
        client.put(
            "/v1/enforcement/challenge",
            json={"question": QUESTION, "answer": ANSWER, "confirm_answer": ANSWER},
        ).status_code
        == 401
    )


def test_first_run_reports_unconfigured_then_persists_setup(client: TestClient) -> None:
    before = client.get("/v1/enforcement/challenge").json()
    assert before == {"configured": False, "question": None}

    created = client.put(
        "/v1/enforcement/challenge",
        json={"question": QUESTION, "answer": ANSWER, "confirm_answer": ANSWER},
    )
    assert created.status_code == 200
    assert created.json()["configured"] is True

    after = client.get("/v1/enforcement/challenge").json()
    assert after["configured"] is True
    assert after["question"] == QUESTION


def test_setup_response_never_carries_the_answer(client: TestClient) -> None:
    client.put(
        "/v1/enforcement/challenge",
        json={"question": QUESTION, "answer": ANSWER, "confirm_answer": ANSWER},
    )
    for endpoint in ("/v1/enforcement/challenge", "/v1/enforcement/status"):
        assert ANSWER not in client.get(endpoint).text


def test_mismatched_confirmation_is_rejected(client: TestClient) -> None:
    response = client.put(
        "/v1/enforcement/challenge",
        json={"question": QUESTION, "answer": ANSWER, "confirm_answer": "different"},
    )
    assert response.status_code == 409
    assert client.get("/v1/enforcement/challenge").json()["configured"] is False


def test_rotation_requires_the_current_answer(client: TestClient) -> None:
    client.put(
        "/v1/enforcement/challenge",
        json={"question": QUESTION, "answer": ANSWER, "confirm_answer": ANSWER},
    )
    blocked = client.put(
        "/v1/enforcement/challenge",
        json={"question": "New question?", "answer": "new-answer", "confirm_answer": "new-answer"},
    )
    assert blocked.status_code == 409
    assert client.get("/v1/enforcement/challenge").json()["question"] == QUESTION

    rotated = client.put(
        "/v1/enforcement/challenge",
        json={
            "question": "New question?",
            "answer": "new-answer",
            "confirm_answer": "new-answer",
            "current_answer": ANSWER,
        },
    )
    assert rotated.status_code == 200
    assert client.get("/v1/enforcement/challenge").json()["question"] == "New question?"


def test_unknown_decision_id_is_rejected(client: TestClient) -> None:
    client.put(
        "/v1/enforcement/challenge",
        json={"question": QUESTION, "answer": ANSWER, "confirm_answer": ANSWER},
    )
    response = client.post(
        "/v1/enforcement/challenge/decision-that-never-existed/respond",
        json={"answer": ANSWER},
    )
    assert response.status_code == 404


def test_enforcement_status_reports_the_committed_default(client: TestClient) -> None:
    status = client.get("/v1/enforcement/status").json()
    assert status["enforcement_enabled"] is False
    assert status["pending_challenge"] is None


def test_correct_scheduled_response_records_an_anchor(
    api_backend_with_sink: tuple[SQLiteApiBackend, ChallengeService, list[dict[str, str]]],
) -> None:
    backend, service, recorded = api_backend_with_sink
    pending = service.open_scheduled(
        user_id="participant-01", session_id="s1", segment_id="seg1"
    )
    backend.respond_to_challenge(
        decision_id=pending.decision_id,
        answer=SCHEDULED_ANSWER,
        response_token=pending.response_token,
    )
    assert len(recorded) == 1
    assert recorded[0]["session_id"] == "s1"
    assert recorded[0]["segment_id"] == "seg1"
    assert recorded[0]["evidence_reference"]


def test_wrong_scheduled_response_records_no_anchor(
    api_backend_with_sink: tuple[SQLiteApiBackend, ChallengeService, list[dict[str, str]]],
) -> None:
    """A wrong answer must never become a verification anchor."""

    backend, service, recorded = api_backend_with_sink
    pending = service.open_scheduled(
        user_id="participant-01", session_id="s1", segment_id="seg1"
    )
    backend.respond_to_challenge(
        decision_id=pending.decision_id,
        answer="wrong-answer",
        response_token=pending.response_token,
    )
    assert recorded == []


def test_ordinary_challenge_response_does_not_use_the_scheduled_sink(
    api_backend_with_sink: tuple[SQLiteApiBackend, ChallengeService, list[dict[str, str]]],
    sample_decision: RiskDecision,
) -> None:
    """A2 anchors continue to flow through EnforcementCoordinator, not here."""

    backend, service, recorded = api_backend_with_sink
    pending = service.open(sample_decision)
    backend.respond_to_challenge(
        decision_id=pending.decision_id,
        answer=SCHEDULED_ANSWER,
        response_token=pending.response_token,
    )
    assert recorded == []
