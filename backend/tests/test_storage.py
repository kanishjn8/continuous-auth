from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
from pydantic import ValidationError

from backend.app.storage.audit import AuditLog
from backend.app.storage.config import StorageSettings, load_storage_settings
from backend.app.storage.errors import (
    StorageConfigError,
    StorageIntegrityError,
    StorageMigrationError,
    StoragePermissionError,
    StorageUnavailableError,
)
from backend.app.storage.service import AvailabilityEvent, StorageService
from protocol.generated.python.contracts import (
    Alert,
    AlertType,
    ApplicationCategory,
    AppRegistryEntry,
    FeatureWindow,
    Health,
    Metrics,
    ModelArtifact,
    RetentionConfig,
    RiskDecision,
    ScoreResult,
    StorageDataPolicy,
    StorageEnvironment,
    UpdateCandidate,
    UserState,
    VerificationAnchor,
)


@pytest.fixture
def settings(tmp_path: Path) -> StorageSettings:
    root = tmp_path / "private-store"
    return StorageSettings(
        config_version="storage-test-1",
        protocol_version="1.0.0",
        environment=StorageEnvironment.DEVELOPMENT,
        data_policy=StorageDataPolicy.SYNTHETIC_ONLY,
        root_directory=root,
        database_path=root / "test.db",
        audit_directory=root / "audit",
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


@pytest.fixture
def storage(
    settings: StorageSettings,
) -> tuple[StorageService, list[AvailabilityEvent]]:
    events: list[AvailabilityEvent] = []
    service = StorageService.open(
        settings,
        availability_sink=events.append,
        permission_hardener=lambda path: path.mkdir(parents=True, exist_ok=True),
    )
    return service, events


def _window(provenance: str = "SYNTHETIC") -> FeatureWindow:
    return FeatureWindow.model_validate(
        {
            "schema_version": "1.0.0",
            "user_id": "synthetic-user",
            "session_id": "synthetic-session",
            "segment_id": "synthetic-segment",
            "window_id": "synthetic-window",
            "t_start_us": 100,
            "t_end_us": 200,
            "quality_label": "INSUFFICIENT_DATA",
            "key_event_count": 0,
            "mouse_event_count": 0,
            "collection_day": "2026-08-26",
            "provenance": provenance,
            "keyboard_features": None,
            "mouse_features": None,
            "context": {
                "dominant_category": "UNKNOWN",
                "category_fractions": {"UNKNOWN": 1.0},
                "app_switch_rate": 0.0,
                "device_class": "UNKNOWN",
            },
        }
    )


def _score() -> ScoreResult:
    unavailable = {
        "available": False,
        "raw_score": None,
        "calibrated_score": None,
        "status": "UNAVAILABLE",
    }
    return ScoreResult.model_validate(
        {
            "schema_version": "1.0.0",
            "user_id": "synthetic-user",
            "profile_version": "synthetic-profile",
            "feature_schema_version": "1.0.0",
            "model_version": "synthetic-model",
            "window_id": "synthetic-window",
            "quality_label": "INSUFFICIENT_DATA",
            "keyboard": unavailable,
            "mouse": unavailable,
        }
    )


def _decision() -> RiskDecision:
    return RiskDecision.model_validate(
        {
            "schema_version": "1.0.0",
            "decision_id": "synthetic-decision",
            "user_id": "synthetic-user",
            "session_id": "synthetic-session",
            "segment_id": "synthetic-segment",
            "window_id": "synthetic-window",
            "t_decision_us": 201,
            "quality_label": "INSUFFICIENT_DATA",
            "fused_score": None,
            "context_confidence": 1.0,
            "confidence_source": "NEUTRAL_FALLBACK",
            "smoothed_score": None,
            "risk_level": "UNAVAILABLE",
            "user_state": "ACTIVE",
            "action": "NONE",
            "reason_code": "INSUFFICIENT_SYNTHETIC_EVIDENCE",
            "threshold_config_version": "synthetic-config",
            "config_checksum": "a" * 64,
            "shadow_mode": True,
            "enforcement_applied": False,
        }
    )


def _seed(service: StorageService) -> None:
    service.upsert_user("synthetic-user", UserState.ACTIVE)
    service.create_session(
        "synthetic-session",
        "synthetic-user",
        VerificationAnchor.A1_LOGIN_UNLOCK,
    )
    service.create_segment(
        "synthetic-segment",
        "synthetic-session",
        100,
        "SYNTHETIC_START",
    )
    service.store_feature_window(_window())


def _table_names(service: StorageService) -> set[str]:
    with service.database.connection() as connection:
        return {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }


def _read_audit(directory: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(directory.glob("audit-*.jsonl*")):
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as handle:
            records.extend(json.loads(line) for line in handle)
    return records


def test_migration_creates_required_tables_wal_indexes_and_is_idempotent(
    storage: tuple[StorageService, list[AvailabilityEvent]],
) -> None:
    service, events = storage
    required = {
        "users",
        "sessions",
        "segments",
        "feature_windows",
        "scores",
        "risk_events",
        "decisions",
        "alerts",
        "models",
        "update_candidates",
        "app_registry",
        "system_metrics",
        "storage_metadata",
        "verification_anchors",
        "model_profiles",
        "update_runs",
    }
    assert required <= _table_names(service)
    with service.database.connection() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 3
        versions = dict(
            connection.execute(
                "SELECT metadata_key, metadata_value FROM storage_metadata"
            ).fetchall()
        )
        assert versions == {
            "database_schema_version": "3",
            "protocol_version": "1.0.0",
            "storage_config_version": "storage-test-1",
        }
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            ).fetchone()[0]
            >= 10
        )
    service.database.initialise()
    assert events == []


def test_modified_applied_migration_is_rejected(
    storage: tuple[StorageService, list[AvailabilityEvent]],
) -> None:
    service, _ = storage
    with service.database.transaction() as connection:
        connection.execute("UPDATE schema_migrations SET checksum = ?", ("0" * 64,))
    with pytest.raises(StorageMigrationError):
        service.database.initialise()


def test_transaction_rolls_back_all_partial_writes(
    storage: tuple[StorageService, list[AvailabilityEvent]],
) -> None:
    service, _ = storage
    with (
        pytest.raises(RuntimeError, match="synthetic rollback"),
        service.database.transaction() as connection,
    ):
        connection.execute(
            """
            INSERT INTO users(user_id, state, schema_version, created_at_utc, updated_at_utc)
            VALUES ('rollback-user', 'ACTIVE', '1.0.0', '2026-08-26T00:00:00Z',
                    '2026-08-26T00:00:00Z')
            """
        )
        raise RuntimeError("synthetic rollback")
    with service.database.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM users WHERE user_id = 'rollback-user'"
            ).fetchone()[0]
            == 0
        )


def test_c2_c3_c4_c6_health_records_persist_and_decision_audit_matches(
    storage: tuple[StorageService, list[AvailabilityEvent]],
) -> None:
    service, events = storage
    _seed(service)
    score = _score()
    decision = _decision()
    service.store_score(score)
    service.store_risk_decision(decision)
    service.store_alert(
        Alert.model_validate(
            {
                "alert_id": "synthetic-alert",
                "alert_type": "AVAILABILITY",
                "severity": "HIGH",
                "code": "SYNTHETIC_STORAGE_CHECK",
                "occurred_at": "2026-08-26T10:00:00Z",
                "acknowledged": False,
            }
        )
    )
    assert service.acknowledge_alert("synthetic-alert")
    assert not service.acknowledge_alert("missing-alert")
    service.set_shadow_mode(True)
    assert service.shadow_mode_enabled()
    service.store_model(
        ModelArtifact.model_validate(
            {
                "schema_version": "1.0.0",
                "artifact_version": "synthetic-artifact",
                "user_id": "synthetic-user",
                "modality": "KEYBOARD",
                "feature_schema_version": "1.0.0",
                "training_range_start": "2026-08-01",
                "training_range_end": "2026-08-02",
                "provenance": ["SYNTHETIC"],
                "hyperparameters": {"fixture": True},
                "calibration": {"fixture": True},
                "metrics": {"fixture": 1.0},
                "checksum": "b" * 64,
            }
        )
    )
    service.store_update_candidate(
        UpdateCandidate.model_validate(
            {
                "schema_version": "1.0.0",
                "candidate_id": "synthetic-candidate",
                "user_id": "synthetic-user",
                "segment_id": "synthetic-segment",
                "gate_evidence": {
                    "g1_risk": True,
                    "g2_verification": True,
                    "g3_volume": True,
                    "g4_continuity": True,
                    "g5_quarantine": True,
                    "g6_schedule": True,
                },
                "verification_anchor": "A1_LOGIN_UNLOCK",
                "quarantined_at": "2026-08-26T10:00:00Z",
                "incident_recorded": False,
                "disposition": "QUARANTINED",
                "reason_code": "SYNTHETIC_FIXTURE",
                "audit_revision": 1,
            }
        )
    )
    observed = datetime(2026, 8, 26, 10, tzinfo=UTC)
    service.record_metrics(
        "collector",
        observed,
        Metrics(
            collector_cpu_percent=1.0,
            collector_memory_bytes=1024,
            dropped_events=0,
            latency_us={"synthetic": 1.0},
        ),
    )
    service.record_health(
        "storage",
        observed,
        Health(
            status="HEALTHY",
            components={"storage": "HEALTHY"},
            heartbeat_age_ms=0,
            collection_paused=False,
        ),
    )

    with service.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM feature_windows").fetchone()[0] == 1
        assert json.loads(connection.execute("SELECT score_json FROM scores").fetchone()[0]) == (
            score.model_dump(mode="json")
        )
        assert json.loads(
            connection.execute("SELECT decision_json FROM risk_events").fetchone()[0]
        ) == decision.model_dump(mode="json")
        assert connection.execute("SELECT count(*) FROM decisions").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM alerts").fetchone()[0] == 1
        assert connection.execute("SELECT acknowledged FROM alerts").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT metadata_value FROM storage_metadata "
                "WHERE metadata_key = 'admin_shadow_mode'"
            ).fetchone()[0]
            == "1"
        )
        assert connection.execute("SELECT count(*) FROM models").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM update_candidates").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM system_metrics").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT count(*) FROM audit_outbox WHERE dispatched_at_utc IS NULL"
            ).fetchone()[0]
            == 0
        )

    records = _read_audit(service.settings.audit_directory)
    audited_decision = next(
        record for record in records if record["record_id"] == "risk-decision:synthetic-decision"
    )
    assert audited_decision["payload"] == decision.model_dump(mode="json")
    assert "ALERT_ACKNOWLEDGED" in {record["event_type"] for record in records}
    assert "SHADOW_MODE_CHANGED" in {record["event_type"] for record in records}
    service.audit.verify_all()
    assert events == []


def test_development_profile_rejects_non_synthetic_provenance(
    storage: tuple[StorageService, list[AvailabilityEvent]],
) -> None:
    service, events = storage
    service.upsert_user("synthetic-user", UserState.ACTIVE)
    service.create_session(
        "synthetic-session", "synthetic-user", VerificationAnchor.A1_LOGIN_UNLOCK
    )
    service.create_segment("synthetic-segment", "synthetic-session", 100, "SYNTHETIC_START")
    with pytest.raises(StorageUnavailableError, match="SYNTHETIC provenance only"):
        service.store_feature_window(_window("PILOT"))
    assert events[-1].alert_type == "AVAILABILITY"
    assert events[-1].severity == "HIGH"


def test_app_registry_is_idempotent_and_rejects_content_like_process_values(
    storage: tuple[StorageService, list[AvailabilityEvent]],
) -> None:
    service, _ = storage
    entry = AppRegistryEntry(
        schema_version="1.0.0",
        app_id=7,
        process_name="synthetic.exe",
        category=ApplicationCategory.UNKNOWN,
    )
    service.register_app(entry)
    service.register_app(entry.model_copy(update={"category": ApplicationCategory.DEVELOPMENT}))
    with service.database.connection() as connection:
        row = connection.execute("SELECT * FROM app_registry WHERE app_id = 7").fetchone()
        assert row["process_name"] == "synthetic.exe"
        assert row["category"] == "DEVELOPMENT"
        assert connection.execute("SELECT count(*) FROM app_registry").fetchone()[0] == 1
    with pytest.raises(ValidationError):
        AppRegistryEntry(
            schema_version="1.0.0",
            app_id=8,
            process_name="C:/private/document.exe",
            category=ApplicationCategory.UNKNOWN,
        )


def test_concurrent_writers_complete_under_wal(
    storage: tuple[StorageService, list[AvailabilityEvent]],
) -> None:
    service, events = storage
    failures: list[Exception] = []

    def write(index: int) -> None:
        try:
            service.register_app(
                AppRegistryEntry(
                    schema_version="1.0.0",
                    app_id=index,
                    process_name=f"synthetic-{index}.exe",
                    category=ApplicationCategory.UNKNOWN,
                )
            )
        except Exception as exc:  # pragma: no cover - assertion reports any thread failure
            failures.append(exc)

    threads = [Thread(target=write, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    with service.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM app_registry").fetchone()[0] == 20
    assert events == []


def test_retention_deletes_bounded_rows_and_reports_counts(
    storage: tuple[StorageService, list[AvailabilityEvent]],
) -> None:
    service, _ = storage
    _seed(service)
    service.store_score(_score())
    service.store_risk_decision(_decision())
    service.store_alert(
        Alert(
            alert_id="retention-alert",
            alert_type=AlertType.AVAILABILITY,
            severity="HIGH",
            code="SYNTHETIC_RETENTION",
            occurred_at="2026-01-01T00:00:00Z",
            acknowledged=False,
        )
    )
    service.record_metrics(
        "collector",
        datetime(2026, 1, 1, tzinfo=UTC),
        Metrics(
            collector_cpu_percent=None,
            collector_memory_bytes=None,
            dropped_events=0,
            latency_us={},
        ),
    )
    now = datetime.now(UTC)
    expired = (now - timedelta(days=8)).isoformat().replace("+00:00", "Z")
    with service.database.transaction() as connection:
        connection.execute("UPDATE feature_windows SET stored_at_utc = ?", (expired,))
        connection.execute("UPDATE scores SET stored_at_utc = ?", (expired,))
        connection.execute("UPDATE risk_events SET stored_at_utc = ?", (expired,))
        connection.execute("UPDATE alerts SET occurred_at_utc = ?", (expired,))
        connection.execute("UPDATE system_metrics SET observed_at_utc = ?", (expired,))
        connection.execute("UPDATE audit_outbox SET occurred_at_utc = ?", (expired,))
    result = service.run_retention(now=now)
    assert result.status == "COMPLETED"
    assert result.feature_windows_deleted == 1
    assert result.scores_deleted == 1
    assert result.risk_events_deleted == 1
    assert result.alerts_deleted == 1
    assert result.system_metrics_deleted == 1
    assert result.audit_outbox_deleted > 0
    with service.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM feature_windows").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM scores").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM risk_events").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM alerts").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM system_metrics").fetchone()[0] == 0


def test_daily_audit_rotation_compression_deletion_and_integrity(tmp_path: Path) -> None:
    current = [datetime(2026, 8, 1, tzinfo=UTC)]
    audit = AuditLog(tmp_path, clock=lambda: current[0])
    audit.append(
        event_type="SYNTHETIC",
        occurred_at_utc="2026-08-01T00:00:00Z",
        correlation_id="old",
        payload={"fixture": 1},
        record_id="old",
    )
    current[0] = datetime(2026, 8, 5, tzinfo=UTC)
    audit.append(
        event_type="SYNTHETIC",
        occurred_at_utc="2026-08-05T00:00:00Z",
        correlation_id="compress",
        payload={"fixture": 2},
        record_id="compress",
    )
    current[0] = datetime(2026, 8, 9, tzinfo=UTC)
    audit.append(
        event_type="SYNTHETIC",
        occurred_at_utc="2026-08-09T00:00:00Z",
        correlation_id="current",
        payload={"fixture": 3},
        record_id="current",
    )
    result = audit.rotate_and_retain(
        now=datetime(2026, 8, 10, tzinfo=UTC),
        compress_after_days=2,
        delete_after_days=7,
    )
    assert result.deleted_files == 1
    assert result.compressed_files == 1
    assert not (tmp_path / "audit-2026-08-01.jsonl").exists()
    assert (tmp_path / "audit-2026-08-05.jsonl.gz").exists()
    assert (tmp_path / "audit-2026-08-09.jsonl").exists()
    audit.verify_all()

    current_file = tmp_path / "audit-2026-08-09.jsonl"
    current_file.write_text(current_file.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
    with pytest.raises(StorageIntegrityError):
        audit.verify_all()


def test_audit_write_failure_is_loud_pending_and_recoverable(
    storage: tuple[StorageService, list[AvailabilityEvent]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, events = storage
    alert = Alert.model_validate(
        {
            "alert_id": "recoverable-alert",
            "alert_type": "AVAILABILITY",
            "severity": "HIGH",
            "code": "SYNTHETIC_DISK_FULL",
            "occurred_at": "2026-08-26T10:00:00Z",
            "acknowledged": False,
        }
    )
    original_append = service.audit.append

    def fail_append(**_: Any) -> str:
        raise OSError(28, "synthetic disk full")

    monkeypatch.setattr(service.audit, "append", fail_append)
    with pytest.raises(StorageUnavailableError):
        service.store_alert(alert)
    assert events[-1].severity == "HIGH"
    with service.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM alerts").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT count(*) FROM audit_outbox WHERE dispatched_at_utc IS NULL"
            ).fetchone()[0]
            == 1
        )

    monkeypatch.setattr(service.audit, "append", original_append)
    assert service.flush_audit() == 1
    records = _read_audit(service.settings.audit_directory)
    assert any(record["record_id"] == "alert:recoverable-alert" for record in records)


def test_initialisation_permission_and_corruption_failures_are_loud(
    settings: StorageSettings,
) -> None:
    permission_events: list[AvailabilityEvent] = []

    def deny(_: Path) -> None:
        raise StoragePermissionError("synthetic permission denial")

    with pytest.raises(StoragePermissionError):
        StorageService.open(
            settings,
            availability_sink=permission_events.append,
            permission_hardener=deny,
        )
    assert permission_events[-1].code == "STORAGE_INITIALISATION_FAILED"

    settings.root_directory.mkdir(parents=True)
    settings.database_path.write_bytes(b"not a sqlite database")
    corruption_events: list[AvailabilityEvent] = []
    with pytest.raises(StorageUnavailableError):
        StorageService.open(
            settings,
            availability_sink=corruption_events.append,
            permission_hardener=lambda path: path.mkdir(parents=True, exist_ok=True),
        )
    assert corruption_events[-1].severity == "HIGH"


def test_config_loader_resolves_environment_and_rejects_unsafe_or_corrupt_config(
    tmp_path: Path,
) -> None:
    config = tmp_path / "storage.yaml"
    config.write_text(
        """
config_version: storage-test-1
protocol_version: 1.0.0
storage:
  environment: DEVELOPMENT
  data_policy: SYNTHETIC_ONLY
  root_directory: "${LOCALAPPDATA}/SyntheticStore"
  database_filename: test.db
  audit_directory_name: audit
  busy_timeout_ms: 1000
retention:
  feature_window_days: 7
  score_days: 7
  audit_compress_after_days: 1
  audit_delete_after_days: 7
  raw_debug_capture_enabled: false
  pilot_mode: false
""".strip(),
        encoding="utf-8",
    )
    settings = load_storage_settings(
        config,
        workspace_root=tmp_path / "workspace",
        environment={"LOCALAPPDATA": str(tmp_path / "local")},
    )
    assert settings.synthetic_only
    assert settings.database_path.name == "test.db"
    with pytest.raises(StorageConfigError, match="missing environment"):
        load_storage_settings(config, environment={})

    text = config.read_text(encoding="utf-8")
    config.write_text(text.replace("audit_delete_after_days: 7", "audit_delete_after_days: 0"))
    with pytest.raises(StorageConfigError, match="C9 validation"):
        load_storage_settings(
            config,
            environment={"LOCALAPPDATA": str(tmp_path / "local")},
        )


def test_database_schema_has_no_forbidden_content_columns(
    storage: tuple[StorageService, list[AvailabilityEvent]],
) -> None:
    service, _ = storage
    forbidden = {
        "keycode",
        "key_code",
        "typed_text",
        "raw_text",
        "window_title",
        "document_name",
        "file_path",
        "url",
        "clipboard",
        "screenshot",
        "screen_content",
    }
    with service.database.connection() as connection:
        for table in _table_names(service):
            columns = {
                str(row["name"]).casefold()
                for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            }
            assert columns.isdisjoint(forbidden)
