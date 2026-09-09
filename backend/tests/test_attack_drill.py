"""Attacker-drill isolation (ADR-014).

The invariant these tests protect is one-way: a drill window is scored, risk
assessed, escalated, enforced, alerted and audited -- and can never become
training data.

Both halves are asserted here. A test that only proved exclusion would pass
just as well against an implementation that silently disabled the drill, and
an unarmed drill measures nothing.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.ingestion import IngestionSettings
from backend.app.risk import load_context_config, load_risk_settings
from backend.app.runtime import RuntimeOrchestrator
from backend.app.storage.audit import AuditLog
from backend.app.storage.config import StorageSettings
from backend.app.storage.database import SQLiteDatabase
from backend.app.storage.drill import DrillTableMissingError, require_drill_table
from backend.app.storage.service import StorageService
from backend.app.models.service import ProfileArtifacts
from backend.app.updates import UpdateManager, load_update_settings
from backend.app.updates.repository import SQLiteUpdateRepository
from ml.features.config import load_config as load_ml_config
from ml.features.schema import DeviceClass, Heartbeat, KeyboardEvent, KeyClass
from protocol.generated.python.contracts import (
    DataProvenance,
    RetentionConfig,
    StorageDataPolicy,
    StorageEnvironment,
    UserState,
)
from tools.collection.repository import load_window_summaries

ROOT = Path(__file__).resolve().parents[2]


def _storage(tmp_path: Path) -> StorageService:
    root = tmp_path / "store"
    root.mkdir()
    audit_directory = root / "audit"
    audit_directory.mkdir()
    settings = StorageSettings(
        config_version="drill-test",
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


def _runtime(
    storage: StorageService,
    *,
    drill_label: str | None = None,
    with_profile: bool = False,
) -> RuntimeOrchestrator:
    def provider(user_id: str) -> ProfileArtifacts | None:
        if not with_profile:
            return None
        # A loadable profile with no modality artifacts. Enough to make the
        # runtime treat the user as having an active profile (so enrollment
        # progress is evaluated at all), without training a real model.
        return ProfileArtifacts(
            user_id=user_id,
            profile_version="drill-test-profile",
            model_version="drill-test-model",
            keyboard=None,
            mouse=None,
        )

    return RuntimeOrchestrator(
        storage=storage,
        ingestion_settings=IngestionSettings(
            config_version="drill-test",
            protocol_version="1.0.0",
            max_frame_bytes=65536,
            raw_event_ring_capacity=16,
            idle_split_seconds=900,
        ),
        ml_config=load_ml_config(),
        risk_settings=load_risk_settings(ROOT / "config/risk.demo-live-single-day.yaml"),
        context_config=load_context_config(),
        provenance=DataProvenance.SYNTHETIC,
        profile_provider=provider,
        heartbeat_timeout_seconds=5,
        measurement_capacity=16,
        update_manager=UpdateManager(load_update_settings(), SQLiteUpdateRepository(storage)),
        drill_label=drill_label,
    )


def _feed_one_window(runtime: RuntimeOrchestrator, *, seq: int = 0) -> None:
    event = KeyboardEvent(
        type="KEY_DOWN",
        t_capture_us=100 + seq,
        key_class=KeyClass.ALPHA_L_HOME,
        is_repeat=False,
        device_class=DeviceClass.UNKNOWN,
        app_id=0,
        seq=seq,
    )
    payload = event.model_dump_json().encode()
    runtime.feed(len(payload).to_bytes(4, "big") + payload)


def _run_session(
    storage: StorageService,
    *,
    session_id: str,
    user_id: str = "synthetic-user",
    day: int = 1,
    drill_label: str | None = None,
    with_profile: bool = False,
) -> RuntimeOrchestrator:
    runtime = _runtime(storage, drill_label=drill_label, with_profile=with_profile)
    runtime.start_authenticated_session(
        user_id=user_id,
        evidence_reference=f"proof-{session_id}",
        authenticated_at=datetime(2026, 1, day, tzinfo=UTC),
        session_id=session_id,
    )
    _feed_one_window(runtime)
    runtime.end_session()
    return runtime


def _window_ids_for_session(storage: StorageService, session_id: str) -> set[str]:
    with storage.database.connection() as connection:
        rows = connection.execute(
            "SELECT window_id FROM feature_windows WHERE session_id = ?", (session_id,)
        ).fetchall()
    return {str(row["window_id"]) for row in rows}


def _seed_enrollment_evidence(storage: StorageService, user_id: str) -> None:
    """Insert enough genuine windows over enough days to clear ENROLLING.

    ``config/risk.demo-live-single-day.yaml`` asks for 20 windows across 1
    distinct day. Seeding them directly keeps the tests fast while still
    exercising the real counting query in the orchestrator.
    """

    context = json.dumps(
        {
            "dominant_category": "PRODUCTIVITY",
            "category_fractions": {"PRODUCTIVITY": 1.0},
            "app_switch_rate": 0.0,
            "device_class": "INTERNAL_KEYBOARD",
            "app_shares": [],
        }
    )
    with storage.database.transaction() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO users(user_id, state, schema_version, "
            "created_at_utc, updated_at_utc) VALUES (?, 'ENROLLING', '1.0.0', ?, ?)",
            (user_id, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO sessions(session_id, user_id, entry_auth_evidence, "
            "started_at_utc, schema_version) VALUES ('seed-session', ?, "
            "'A1_LOGIN_UNLOCK', '2026-01-01T00:00:00Z', '1.0.0')",
            (user_id,),
        )
        connection.execute(
            "INSERT INTO segments(segment_id, session_id, started_at_capture_us, "
            "boundary_reason, schema_version) VALUES "
            "('seed-segment', 'seed-session', 0, 'SESSION_START', '1.0.0')"
        )
        for index in range(25):
            connection.execute(
                """
                INSERT INTO feature_windows(
                    window_id, user_id, session_id, segment_id, t_start_us, t_end_us,
                    quality_label, key_event_count, mouse_event_count, collection_day,
                    provenance, keyboard_features_json, mouse_features_json,
                    context_json, schema_version, stored_at_utc
                ) VALUES (?, ?, 'seed-session', 'seed-segment', ?, ?,
                          'INSUFFICIENT_DATA', 0, 0, ?, 'SYNTHETIC', NULL, NULL,
                          ?, '1.0.0', ?)
                """,
                (
                    f"seed-window-{index}",
                    user_id,
                    index * 1000,
                    index * 1000 + 500,
                    "2026-01-01",
                    context,
                    "2026-01-01T00:00:00Z",
                ),
            )


# ---------------------------------------------------------------------------
# The drill must actually work. Suppression is about training, not scoring.
# ---------------------------------------------------------------------------


def test_drill_windows_are_scored_and_stored(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    _run_session(storage, session_id="drill-1", drill_label="drill-01")

    with storage.database.connection() as connection:
        windows = connection.execute("SELECT COUNT(*) AS n FROM feature_windows").fetchone()
        scores = connection.execute("SELECT COUNT(*) AS n FROM scores").fetchone()
    assert windows["n"] == 1
    assert scores["n"] == 1


def test_live_risk_state_transitions_are_not_suppressed_during_a_drill(tmp_path: Path) -> None:
    """Enrollment progress is suppressed; the live risk state machine is not.

    This is the distinction the drill exists to preserve. A drill that could
    not move the risk state would not be testing the live system at all.
    """
    storage = _storage(tmp_path)
    runtime = _runtime(storage, drill_label="drill-01", with_profile=True)
    runtime.start_authenticated_session(
        user_id="synthetic-user",
        evidence_reference="proof",
        authenticated_at=datetime(2026, 1, 1, tzinfo=UTC),
        session_id="drill-1",
    )
    _feed_one_window(runtime)
    assert runtime._risk_engine is not None

    # A live availability transition during the drill. The drill suppresses
    # enrollment and calibration bookkeeping; it must not suppress this.
    runtime._last_heartbeat_arrival = 0.0
    runtime.check_heartbeat(now=runtime.heartbeat_timeout_seconds + 1.0)
    assert runtime._risk_engine.state_machine.state is UserState.DEGRADED
    runtime.end_session()

    with storage.database.connection() as connection:
        risk_events = connection.execute("SELECT COUNT(*) AS n FROM risk_events").fetchone()
        decisions = connection.execute("SELECT COUNT(*) AS n FROM decisions").fetchone()
    assert risk_events["n"] >= 1
    assert decisions["n"] >= 1


# ---------------------------------------------------------------------------
# Corpus exclusion, at the single centralized filter.
# ---------------------------------------------------------------------------


def test_drill_windows_are_excluded_from_load_window_summaries(tmp_path: Path) -> None:
    """The one filter that closes health, freeze, verify and corpus loading."""
    storage = _storage(tmp_path)
    _run_session(storage, session_id="genuine-1", day=1)
    _run_session(storage, session_id="drill-1", day=2, drill_label="drill-01")

    drill_windows = _window_ids_for_session(storage, "drill-1")
    genuine_windows = _window_ids_for_session(storage, "genuine-1")
    assert drill_windows and genuine_windows

    summaries = {summary.window_id for summary in load_window_summaries(storage.settings.database_path)}
    assert summaries.isdisjoint(drill_windows)
    # And the filter must not be over-broad: genuine data still gets through.
    assert genuine_windows <= summaries


def test_a_manifest_built_from_the_summaries_cannot_contain_a_drill_window(
    tmp_path: Path,
) -> None:
    """build_freeze consumes load_window_summaries, so exclusion cascades."""
    storage = _storage(tmp_path)
    _run_session(storage, session_id="genuine-1", day=1)
    _run_session(storage, session_id="drill-1", day=2, drill_label="drill-01")

    drill_windows = _window_ids_for_session(storage, "drill-1")
    summaries = load_window_summaries(storage.settings.database_path)
    assert {summary.window_id for summary in summaries}.isdisjoint(drill_windows)


def test_corpus_loaders_refuse_a_database_without_the_drill_table(tmp_path: Path) -> None:
    """Fail-safe: an un-migrated database cannot prove drills were excluded."""
    path = tmp_path / "unmigrated.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE feature_windows(window_id TEXT)")
    connection.commit()
    with pytest.raises(DrillTableMissingError, match="DRILL_TABLE_MISSING"):
        require_drill_table(connection)
    connection.close()


# ---------------------------------------------------------------------------
# Update pipeline, login anchor, enrollment progress, context learning.
# ---------------------------------------------------------------------------


def test_drill_segments_never_become_update_candidates(tmp_path: Path) -> None:
    """Not even a REJECTED candidate: attacker evidence stays out entirely."""
    storage = _storage(tmp_path)
    _run_session(storage, session_id="drill-1", drill_label="drill-01")
    with storage.database.connection() as connection:
        candidates = connection.execute("SELECT COUNT(*) AS n FROM update_candidates").fetchone()
    assert candidates["n"] == 0


def test_a_genuine_session_still_submits_an_update_candidate(tmp_path: Path) -> None:
    """Control for the test above: the suppression is drill-specific."""
    storage = _storage(tmp_path)
    _run_session(storage, session_id="genuine-1")
    with storage.database.connection() as connection:
        candidates = connection.execute("SELECT COUNT(*) AS n FROM update_candidates").fetchone()
    assert candidates["n"] == 1


def test_no_login_anchor_is_recorded_for_a_drill_session(tmp_path: Path) -> None:
    """The attacker did not authenticate, so they get no A1.

    G2 exists to withhold exactly this evidence from unverified activity.
    """
    storage = _storage(tmp_path)
    _run_session(storage, session_id="drill-1", drill_label="drill-01")
    with storage.database.connection() as connection:
        anchors = connection.execute("SELECT COUNT(*) AS n FROM verification_anchors").fetchone()
    assert anchors["n"] == 0


def test_a_drill_cannot_overwrite_the_legitimate_users_existing_anchor(tmp_path: Path) -> None:
    """An immediate takeover must not silently redefine the anchor as genuine."""
    storage = _storage(tmp_path)
    _run_session(storage, session_id="genuine-1", day=1)
    with storage.database.connection() as connection:
        before = connection.execute(
            "SELECT session_id, anchor_type, evidence_reference "
            "FROM verification_anchors ORDER BY session_id"
        ).fetchall()
    assert len(before) == 1

    _run_session(storage, session_id="drill-1", day=2, drill_label="drill-01")
    with storage.database.connection() as connection:
        after = connection.execute(
            "SELECT session_id, anchor_type, evidence_reference "
            "FROM verification_anchors ORDER BY session_id"
        ).fetchall()
    assert [tuple(row) for row in after] == [tuple(row) for row in before]


def test_drill_windows_do_not_advance_enrollment_progress(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    _seed_enrollment_evidence(storage, "synthetic-user")
    _run_session(
        storage, session_id="drill-1", day=2, drill_label="drill-01", with_profile=True
    )
    with storage.database.connection() as connection:
        state = connection.execute(
            "SELECT state FROM users WHERE user_id = ?", ("synthetic-user",)
        ).fetchone()
    assert UserState(state["state"]) is UserState.ENROLLING


def test_a_genuine_session_does_advance_enrollment_progress(tmp_path: Path) -> None:
    """Control for the test above -- otherwise the suppression proves nothing."""
    storage = _storage(tmp_path)
    _seed_enrollment_evidence(storage, "synthetic-user")
    _run_session(storage, session_id="genuine-1", day=2, with_profile=True)
    with storage.database.connection() as connection:
        state = connection.execute(
            "SELECT state FROM users WHERE user_id = ?", ("synthetic-user",)
        ).fetchone()
    assert UserState(state["state"]) is not UserState.ENROLLING


def test_context_learning_is_suspended_during_a_drill(tmp_path: Path) -> None:
    """In-memory only, so it cannot reach a model -- but it would distort the
    drill's own measurement, which is the number the drill exists to produce.
    """
    storage = _storage(tmp_path)
    runtime = _runtime(storage, drill_label="drill-01")
    assert runtime.context_layer.learning_suspended() is True
    before = dict(runtime.context_layer.statistics())
    _run_session(storage, session_id="drill-1", drill_label="drill-01")
    assert dict(runtime.context_layer.statistics()) == before


def test_context_learning_is_active_outside_a_drill(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    runtime = _runtime(storage)
    assert runtime.context_layer.learning_suspended() is False


def test_a_drill_session_is_recorded_and_queryable(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    _run_session(storage, session_id="drill-1", drill_label="drill-01")
    assert storage.is_drill_session("drill-1") is True
    assert storage.is_drill_session("nonexistent") is False
    with storage.database.connection() as connection:
        row = connection.execute(
            "SELECT drill_label FROM drill_sessions WHERE session_id = ?", ("drill-1",)
        ).fetchone()
    assert row["drill_label"] == "drill-01"


def test_a_blank_drill_label_is_refused(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    with pytest.raises(ValueError, match="drill label must not be blank"):
        _runtime(storage, drill_label="   ")


# ---------------------------------------------------------------------------
# Calibration progression and DEGRADED recovery (ADR-014 Phase D).
#
# These share this module's runtime harness rather than duplicating it.
# ---------------------------------------------------------------------------


def _seed_scores(
    storage: StorageService,
    user_id: str,
    *,
    count: int,
    profile_version: str,
    available: bool,
) -> None:
    """Seed `scores` rows for the calibration-counting tests.

    Scores written while no model existed carry profile_version
    'unavailable'. The live pilot database holds 1221 of them; counting them
    let a user reach ACTIVE within one window of activation, skipping the
    PLAN.md Section 5.3 shadow period entirely.
    """

    score_json = json.dumps(
        {
            "keyboard": {"available": available, "status": "SCORED" if available else "UNAVAILABLE"},
            "mouse": {"available": available, "status": "SCORED" if available else "UNAVAILABLE"},
        }
    )
    with storage.database.transaction() as connection:
        for index in range(count):
            connection.execute(
                """
                INSERT INTO scores(
                    user_id, window_id, profile_version, feature_schema_version,
                    model_version, quality_label, score_json, schema_version, stored_at_utc
                ) VALUES (?, ?, ?, '1.0.0', 'unavailable', 'FULL', ?, '1.0.0', ?)
                """,
                (
                    user_id,
                    f"seed-window-{index}",
                    profile_version,
                    score_json,
                    "2026-01-01T00:00:00Z",
                ),
            )


def test_pre_activation_scores_do_not_satisfy_calibration(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    _seed_enrollment_evidence(storage, "synthetic-user")
    _seed_scores(
        storage, "synthetic-user", count=50, profile_version="unavailable", available=True
    )

    _run_session(storage, session_id="genuine-1", day=2, with_profile=True)

    with storage.database.connection() as connection:
        state = connection.execute(
            "SELECT state FROM users WHERE user_id = ?", ("synthetic-user",)
        ).fetchone()
    # Enrollment evidence is met, so CALIBRATING is reached -- but the 50
    # pre-activation rows must not carry the user straight through to ACTIVE.
    assert UserState(state["state"]) is UserState.CALIBRATING


def test_windows_with_no_available_modality_do_not_count_toward_calibration(
    tmp_path: Path,
) -> None:
    """An idle lunch break must not age a user into ACTIVE.

    These rows carry the CURRENT profile version, so the version equality
    alone does not exclude them -- only the availability filter does.
    """
    storage = _storage(tmp_path)
    _seed_enrollment_evidence(storage, "synthetic-user")
    _seed_scores(
        storage,
        "synthetic-user",
        count=50,
        profile_version="drill-test-profile",
        available=False,
    )

    _run_session(storage, session_id="genuine-1", day=2, with_profile=True)

    with storage.database.connection() as connection:
        state = connection.execute(
            "SELECT state FROM users WHERE user_id = ?", ("synthetic-user",)
        ).fetchone()
    assert UserState(state["state"]) is UserState.CALIBRATING


def test_post_activation_scored_windows_do_complete_calibration(tmp_path: Path) -> None:
    """Control: calibration is reachable, so the filters are not just refusing."""
    storage = _storage(tmp_path)
    _seed_enrollment_evidence(storage, "synthetic-user")
    _seed_scores(
        storage,
        "synthetic-user",
        count=50,
        profile_version="drill-test-profile",
        available=True,
    )

    _run_session(storage, session_id="genuine-1", day=2, with_profile=True)

    with storage.database.connection() as connection:
        state = connection.execute(
            "SELECT state FROM users WHERE user_id = ?", ("synthetic-user",)
        ).fetchone()
    assert UserState(state["state"]) is UserState.ACTIVE


def test_degraded_recovers_when_the_heartbeat_resumes(tmp_path: Path) -> None:
    """DEGRADED is recoverable, not a one-way door (ADR-011).

    Without this a single heartbeat gap suspends enforcement for the rest of
    the backend process, silently.
    """
    storage = _storage(tmp_path)
    _seed_enrollment_evidence(storage, "synthetic-user")
    runtime = _runtime(storage, with_profile=True)
    runtime.start_authenticated_session(
        user_id="synthetic-user",
        evidence_reference="proof",
        authenticated_at=datetime(2026, 1, 2, tzinfo=UTC),
        session_id="genuine-1",
    )
    assert runtime._risk_engine is not None
    before = runtime._risk_engine.state_machine.state

    runtime._last_heartbeat_arrival = 0.0
    runtime.check_heartbeat(now=runtime.heartbeat_timeout_seconds + 1.0)
    assert runtime._risk_engine.state_machine.state is UserState.DEGRADED

    runtime._handle_heartbeat(
        _heartbeat()
    )
    assert runtime._risk_engine.state_machine.state is before


def test_degrade_and_recover_are_both_persisted(tmp_path: Path) -> None:
    """The users table must not contradict the emitted decision records."""
    storage = _storage(tmp_path)
    _seed_enrollment_evidence(storage, "synthetic-user")
    runtime = _runtime(storage, with_profile=True)
    runtime.start_authenticated_session(
        user_id="synthetic-user",
        evidence_reference="proof",
        authenticated_at=datetime(2026, 1, 2, tzinfo=UTC),
        session_id="genuine-1",
    )

    def stored_state() -> UserState:
        with storage.database.connection() as connection:
            row = connection.execute(
                "SELECT state FROM users WHERE user_id = ?", ("synthetic-user",)
            ).fetchone()
        return UserState(row["state"])

    before = stored_state()
    runtime._last_heartbeat_arrival = 0.0
    runtime.check_heartbeat(now=runtime.heartbeat_timeout_seconds + 1.0)
    assert stored_state() is UserState.DEGRADED

    runtime._handle_heartbeat(
        _heartbeat()
    )
    assert stored_state() is before


def _heartbeat() -> Heartbeat:
    return Heartbeat(
        type="HEARTBEAT",
        t_capture_us=1,
        seq=1,
        collector_uptime_ms=1000,
        dropped_events=0,
        buffer_high_water=0,
        collection_paused=False,
    )
