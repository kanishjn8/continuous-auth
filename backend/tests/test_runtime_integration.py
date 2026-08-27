from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backend.app.ingestion import IngestionSettings
from backend.app.risk import load_context_config, load_risk_settings
from backend.app.runtime import RuntimeOrchestrator
from backend.app.storage.audit import AuditLog
from backend.app.storage.config import StorageSettings
from backend.app.storage.database import SQLiteDatabase
from backend.app.storage.service import StorageService
from backend.app.updates import UpdateManager, load_update_settings
from backend.app.updates.repository import SQLiteUpdateRepository
from ml.features.config import load_config as load_ml_config
from ml.features.schema import DeviceClass, KeyboardEvent, KeyClass
from protocol.generated.python.contracts import (
    DataProvenance,
    RetentionConfig,
    StorageDataPolicy,
    StorageEnvironment,
)


def _storage(tmp_path: Path) -> StorageService:
    root = tmp_path / "store"
    root.mkdir()
    audit_directory = root / "audit"
    audit_directory.mkdir()
    settings = StorageSettings(
        config_version="runtime-test",
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


def test_frame_reaches_shared_window_storage_and_clean_lifecycle(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    runtime = RuntimeOrchestrator(
        storage=storage,
        ingestion_settings=IngestionSettings(
            config_version="runtime-test",
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
        update_manager=UpdateManager(load_update_settings(), SQLiteUpdateRepository(storage)),
    )
    lifecycle, _ = runtime.start_authenticated_session(
        user_id="synthetic-user",
        evidence_reference="synthetic-entry-proof",
        authenticated_at=datetime(2026, 1, 1, tzinfo=UTC),
        session_id="session-1",
    )
    assert lifecycle.kind == "SESSION_STARTED"
    event = KeyboardEvent(
        type="KEY_DOWN",
        t_capture_us=100,
        key_class=KeyClass.ALPHA_L_HOME,
        is_repeat=False,
        device_class=DeviceClass.UNKNOWN,
        app_id=0,
        seq=0,
    )
    payload = event.model_dump_json().encode()
    framed = len(payload).to_bytes(4, "big") + payload
    first, _ = runtime.feed(framed[:3])
    second, _ = runtime.feed(framed[3:])
    assert not first.events
    assert len(second.events) == 1
    runtime.end_session()

    with storage.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM feature_windows").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM verification_anchors").fetchone()[0] == 1
        candidate = connection.execute("SELECT candidate_json FROM update_candidates").fetchone()
        assert candidate is not None
        candidate_document = candidate["candidate_json"]
        assert '"g2_verification":true' in candidate_document
        assert '"g3_volume":false' in candidate_document
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sessions WHERE ended_at_utc IS NOT NULL"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM segments WHERE ended_at_capture_us IS NOT NULL"
            ).fetchone()[0]
            == 1
        )


def test_sequential_users_reset_ordering_and_never_share_windows(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    profile_requests: list[str] = []

    def profile_provider(user_id: str) -> None:
        profile_requests.append(user_id)
        return None

    runtime = RuntimeOrchestrator(
        storage=storage,
        ingestion_settings=IngestionSettings(
            config_version="runtime-test",
            protocol_version="1.0.0",
            max_frame_bytes=65536,
            raw_event_ring_capacity=16,
            idle_split_seconds=900,
        ),
        ml_config=load_ml_config(),
        risk_settings=load_risk_settings(),
        context_config=load_context_config(),
        provenance=DataProvenance.SYNTHETIC,
        profile_provider=profile_provider,
        heartbeat_timeout_seconds=5,
        measurement_capacity=16,
    )
    for index, user_id in enumerate(("synthetic-user-a", "synthetic-user-b")):
        runtime.start_authenticated_session(
            user_id=user_id,
            evidence_reference=f"synthetic-proof-{index}",
            authenticated_at=datetime(2026, 1, index + 1, tzinfo=UTC),
            session_id=f"session-{index}",
        )
        event = KeyboardEvent(
            type="KEY_DOWN",
            t_capture_us=100,
            key_class=KeyClass.ALPHA_L_HOME,
            is_repeat=False,
            device_class=DeviceClass.UNKNOWN,
            app_id=0,
            seq=0,
        )
        payload = event.model_dump_json().encode()
        runtime.feed(len(payload).to_bytes(4, "big") + payload)
        runtime.end_session()

    with storage.database.connection() as connection:
        rows = connection.execute(
            """
            SELECT user_id, COUNT(*) AS window_count
            FROM feature_windows GROUP BY user_id ORDER BY user_id
            """
        ).fetchall()
    assert [(row["user_id"], row["window_count"]) for row in rows] == [
        ("synthetic-user-a", 1),
        ("synthetic-user-b", 1),
    ]
    assert {"synthetic-user-a", "synthetic-user-b"}.issubset(profile_requests)
