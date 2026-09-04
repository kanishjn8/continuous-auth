"""A3 prompt scheduling: only a successful A3 resets the clock."""

from __future__ import annotations

import datetime as datetime_module
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self

import pytest

from backend.app.decisions.adapters import EnforcementCoordinator
from backend.app.decisions.challenge import ChallengeService
from backend.app.decisions.config import EnforcementSettings
from backend.app.ingestion import IngestionSettings
from backend.app.risk import load_context_config, load_risk_settings
from backend.app.runtime import RuntimeOrchestrator
from backend.app.runtime import orchestrator as orchestrator_module
from backend.app.storage.audit import AuditLog
from backend.app.storage.config import StorageSettings
from backend.app.storage.database import SQLiteDatabase
from backend.app.storage.service import StorageService
from backend.app.updates.anchors import ScheduledAnchorScheduler
from ml.features.config import load_config as load_ml_config
from protocol.generated.python.contracts import (
    DataProvenance,
    RetentionConfig,
    StorageDataPolicy,
    StorageEnvironment,
)

FOUR_HOURS = 4 * 3600.0
START = datetime(2026, 9, 3, 9, 0, tzinfo=UTC)


def _scheduler() -> ScheduledAnchorScheduler:
    scheduler = ScheduledAnchorScheduler(FOUR_HOURS)
    scheduler.session_started(session_id="s1", at=START)
    return scheduler


def test_not_due_before_the_interval_elapses() -> None:
    scheduler = _scheduler()
    assert scheduler.due(session_id="s1", now=START + timedelta(hours=3, minutes=59)) is False


def test_due_once_the_interval_elapses() -> None:
    scheduler = _scheduler()
    assert scheduler.due(session_id="s1", now=START + timedelta(hours=4)) is True


def test_recording_an_anchor_resets_the_clock() -> None:
    scheduler = _scheduler()
    at = START + timedelta(hours=4)
    assert scheduler.due(session_id="s1", now=at) is True
    scheduler.anchor_recorded(session_id="s1", at=at)
    assert scheduler.due(session_id="s1", now=at + timedelta(hours=3)) is False
    assert scheduler.due(session_id="s1", now=at + timedelta(hours=4)) is True


def test_unknown_session_is_never_due() -> None:
    scheduler = ScheduledAnchorScheduler(FOUR_HOURS)
    assert scheduler.due(session_id="absent", now=START) is False


def test_ended_session_is_never_due() -> None:
    scheduler = _scheduler()
    scheduler.session_ended(session_id="s1")
    assert scheduler.due(session_id="s1", now=START + timedelta(hours=9)) is False


def test_naive_timestamps_are_rejected() -> None:
    scheduler = ScheduledAnchorScheduler(FOUR_HOURS)
    with pytest.raises(ValueError):
        scheduler.session_started(session_id="s1", at=datetime(2026, 9, 3, 9, 0))


def test_non_positive_interval_is_rejected() -> None:
    with pytest.raises(ValueError):
        ScheduledAnchorScheduler(0.0)


def test_scheduler_is_not_due_without_an_active_session() -> None:
    """A prompt is only meaningful inside a session that can be anchored."""

    scheduler = ScheduledAnchorScheduler(FOUR_HOURS)
    assert scheduler.due(session_id="s1", now=START + timedelta(days=1)) is False


def test_interval_comes_from_update_settings() -> None:
    """The cadence is configuration, never a literal in code (AGENTS.md 7)."""

    from backend.app.updates.config import load_update_settings

    root = Path(__file__).resolve().parents[2]
    settings = load_update_settings(root / "config/updates.development.yaml")
    seconds = settings.update_manager.scheduled_anchor_interval_seconds
    scheduler = ScheduledAnchorScheduler(seconds)
    scheduler.session_started(session_id="s1", at=START)
    assert scheduler.due(session_id="s1", now=START + timedelta(seconds=seconds - 1)) is False
    assert scheduler.due(session_id="s1", now=START + timedelta(seconds=seconds)) is True


def test_collection_and_runtime_cadences_agree() -> None:
    """Two configs describe one concept; they must not drift apart."""

    from backend.app.updates.config import load_update_settings
    from tools.collection.config import load_collection_settings

    root = Path(__file__).resolve().parents[2]
    runtime_seconds = load_update_settings(
        root / "config/updates.development.yaml"
    ).update_manager.scheduled_anchor_interval_seconds
    collection_hours = load_collection_settings(
        root / "config/collection.pilot.yaml"
    ).scheduled_anchor_interval_hours
    assert collection_hours * 3600 == runtime_seconds


# -- wiring: RuntimeOrchestrator + ScheduledAnchorScheduler + ChallengeService ----

QUESTION = "What was the name of your first pet?"
ANSWER = "wellington-the-third"


def _storage(tmp_path: Path) -> StorageService:
    root = tmp_path / "store"
    root.mkdir()
    audit_directory = root / "audit"
    audit_directory.mkdir()
    settings = StorageSettings(
        config_version="scheduled-anchor-test",
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


def _orchestrator(
    tmp_path: Path, *, interval_seconds: float = FOUR_HOURS, configure_challenge: bool = True
) -> RuntimeOrchestrator:
    storage = _storage(tmp_path)
    enforcement_settings = EnforcementSettings(
        config_version="scheduled-anchor-test",
        enabled=False,
        native_prompt=True,
        lock_workstation=False,
        challenge_timeout_seconds=120.0,
        answer_hash_iterations=100_000,
    )
    challenge_service = ChallengeService(storage, enforcement_settings)
    if configure_challenge:
        challenge_service.configure(question=QUESTION, answer=ANSWER, confirm_answer=ANSWER)
    return RuntimeOrchestrator(
        storage=storage,
        ingestion_settings=IngestionSettings(
            config_version="scheduled-anchor-test",
            protocol_version="1.0.0",
            max_frame_bytes=65536,
            raw_event_ring_capacity=16,
            idle_split_seconds=900,
        ),
        ml_config=load_ml_config(),
        risk_settings=load_risk_settings(),
        context_config=load_context_config(),
        provenance=DataProvenance.SYNTHETIC,
        profile_provider=lambda user_id: None,
        heartbeat_timeout_seconds=5,
        measurement_capacity=16,
        enforcement=EnforcementCoordinator({}, store=storage, notice_sink=lambda notice: None),
        anchor_scheduler=ScheduledAnchorScheduler(interval_seconds),
        challenge_service=challenge_service,
    )


def test_starting_a_session_seeds_the_scheduler(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    lifecycle, _ = orchestrator.start_authenticated_session(
        user_id="participant-1",
        evidence_reference="entry-proof",
        authenticated_at=START,
        session_id="session-1",
    )
    assert orchestrator.anchor_scheduler is not None
    assert lifecycle.session_id is not None
    assert (
        orchestrator.anchor_scheduler.due(
            session_id=lifecycle.session_id, now=START + timedelta(hours=4)
        )
        is True
    )
    assert (
        orchestrator.anchor_scheduler.due(
            session_id=lifecycle.session_id, now=START + timedelta(hours=3)
        )
        is False
    )


def test_check_scheduled_anchor_is_none_without_an_active_session(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    assert orchestrator.check_scheduled_anchor(now=START + timedelta(hours=10)) is None


def test_check_scheduled_anchor_opens_a_challenge_when_due(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    orchestrator.start_authenticated_session(
        user_id="participant-1",
        evidence_reference="entry-proof",
        authenticated_at=START,
        session_id="session-1",
    )
    # No segment has been opened yet, so there is nothing to attach a prompt to.
    assert orchestrator.check_scheduled_anchor(now=START + timedelta(hours=4)) is None

    orchestrator._builders["segment-1"] = object()  # type: ignore[assignment]
    pending = orchestrator.check_scheduled_anchor(now=START + timedelta(hours=4))
    assert pending is not None
    assert pending.user_id == "participant-1"
    assert pending.session_id == "session-1"
    assert pending.segment_id == "segment-1"
    assert pending.question == QUESTION


def test_check_scheduled_anchor_fails_open_without_a_configured_challenge(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path, configure_challenge=False)
    orchestrator.start_authenticated_session(
        user_id="participant-1",
        evidence_reference="entry-proof",
        authenticated_at=START,
        session_id="session-1",
    )
    orchestrator._builders["segment-1"] = object()  # type: ignore[assignment]
    assert orchestrator.check_scheduled_anchor(now=START + timedelta(hours=4)) is None


def test_complete_scheduled_anchor_records_and_resets_the_clock(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    lifecycle, _ = orchestrator.start_authenticated_session(
        user_id="participant-1",
        evidence_reference="entry-proof",
        authenticated_at=START,
        session_id="session-1",
    )
    session_id = lifecycle.session_id
    assert session_id is not None
    orchestrator.storage.create_segment("segment-1", session_id, 0, "SESSION_START")
    due_at = START + timedelta(hours=4)
    assert orchestrator.anchor_scheduler is not None
    assert orchestrator.anchor_scheduler.due(session_id=session_id, now=due_at) is True

    orchestrator.complete_scheduled_anchor(
        decision_id="scheduled-anchor:test",
        session_id=session_id,
        segment_id="segment-1",
        evidence_reference="challenge:scheduled-anchor:test:proof",
        at=due_at,
    )

    assert (
        orchestrator.anchor_scheduler.due(session_id=session_id, now=due_at + timedelta(hours=3))
        is False
    )
    assert (
        orchestrator.anchor_scheduler.due(session_id=session_id, now=due_at + timedelta(hours=4))
        is True
    )
    with orchestrator.storage.database.connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM verification_anchors WHERE anchor_type = 'A3_SCHEDULED_PROMPT'"
        ).fetchone()[0]
    assert count == 1


def test_check_heartbeat_triggers_the_scheduled_anchor_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: check_heartbeat must actually call check_scheduled_anchor.

    check_scheduled_anchor is fully covered by direct-call tests above, but
    none of them prove the heartbeat path wires into it. A3 is the only
    mechanism that anchors an otherwise uneventful session, and real
    collection cannot be redone after the fact, so the one line in
    check_heartbeat that dispatches this check needs its own regression
    test: this test must fail if that line is ever deleted.
    """

    orchestrator = _orchestrator(tmp_path)
    lifecycle, _ = orchestrator.start_authenticated_session(
        user_id="participant-1",
        evidence_reference="entry-proof",
        authenticated_at=START,
        session_id="session-1",
    )
    session_id = lifecycle.session_id
    assert session_id is not None
    orchestrator.storage.create_segment("segment-1", session_id, 0, "SESSION_START")
    orchestrator._builders["segment-1"] = object()  # type: ignore[assignment]

    due_at = START + timedelta(hours=4)

    class _FixedDatetime(datetime_module.datetime):
        @classmethod
        def now(cls, tz: datetime_module.tzinfo | None = None) -> Self:
            return due_at  # type: ignore[return-value]

    monkeypatch.setattr(orchestrator_module, "datetime", _FixedDatetime)

    assert orchestrator.challenge_service is not None
    assert orchestrator.challenge_service.pending() == ()

    # No heartbeat has arrived yet, so check_heartbeat takes its healthy
    # (age is None) early-return path -- exactly the path the scheduled
    # anchor check is dispatched from.
    orchestrator.check_heartbeat()

    pending = orchestrator.challenge_service.pending()
    assert len(pending) == 1
    assert pending[0].session_id == session_id
    assert pending[0].segment_id == "segment-1"
