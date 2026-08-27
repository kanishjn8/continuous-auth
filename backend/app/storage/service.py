"""Typed repositories and fail-open availability signaling for T-009."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

from protocol.generated.python.contracts import (
    PROTOCOL_VERSION,
    Alert,
    AppRegistryEntry,
    DataProvenance,
    FeatureWindow,
    Health,
    Metrics,
    ModelArtifact,
    RiskDecision,
    ScoreResult,
    UpdateCandidate,
    UserState,
    VerificationAnchor,
)

from .audit import AuditLog
from .config import StorageSettings
from .database import SQLiteDatabase
from .errors import StorageError, StorageUnavailableError
from .permissions import restrict_directory, restrict_file
from .retention import RetentionResult, run_retention

if TYPE_CHECKING:
    from backend.app.decisions.adapters import ActionOutcome, VerificationRecord

LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_text(value: datetime | None = None) -> str:
    return (
        (value or _utc_now())
        .astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _enum_value(value: Enum | str) -> str:
    return str(value.value) if isinstance(value, Enum) else value


def _json(value: BaseModel | Mapping[str, Any]) -> str:
    document = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class AvailabilityEvent:
    """A loud signal only; it deliberately contains no enforcement action."""

    alert_type: str
    severity: str
    code: str
    operation: str
    occurred_at_utc: str
    detail: str


AvailabilitySink = Callable[[AvailabilityEvent], None]
PermissionHardener = Callable[[Path], None]


class StorageService:
    """Persist only validated aggregate contracts and lifecycle metadata."""

    def __init__(
        self,
        settings: StorageSettings,
        database: SQLiteDatabase,
        audit: AuditLog,
        availability_sink: AvailabilitySink | None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.audit = audit
        self._availability_sink = availability_sink

    @classmethod
    def open(
        cls,
        settings: StorageSettings,
        *,
        availability_sink: AvailabilitySink | None = None,
        permission_hardener: PermissionHardener = restrict_directory,
    ) -> StorageService:
        """Initialise private WAL storage, emitting a loud event on any failure."""

        try:
            permission_hardener(settings.root_directory)
            permission_hardener(settings.audit_directory)
            database = SQLiteDatabase(settings.database_path, settings.busy_timeout_ms)
            database.initialise()
            restrict_file(settings.database_path)
            cls._record_active_versions(database, settings)
            audit = AuditLog(settings.audit_directory)
            service = cls(settings, database, audit, availability_sink)
            service.flush_audit()
            return service
        except Exception as exc:
            cls._emit_to(availability_sink, "STORAGE_INITIALISATION_FAILED", "initialise", exc)
            if isinstance(exc, StorageError):
                raise
            raise StorageUnavailableError("storage initialisation failed") from exc

    @staticmethod
    def _record_active_versions(
        database: SQLiteDatabase,
        settings: StorageSettings,
    ) -> None:
        now = _utc_text()
        with database.transaction() as connection:
            schema_version = str(connection.execute("PRAGMA user_version").fetchone()[0])
            values = {
                "database_schema_version": schema_version,
                "storage_config_version": settings.config_version,
                "protocol_version": settings.protocol_version,
            }
            connection.executemany(
                """
                INSERT INTO storage_metadata(metadata_key, metadata_value, updated_at_utc)
                VALUES (?, ?, ?)
                ON CONFLICT(metadata_key) DO UPDATE SET
                    metadata_value = excluded.metadata_value,
                    updated_at_utc = excluded.updated_at_utc
                """,
                ((key, value, now) for key, value in values.items()),
            )

    @staticmethod
    def _emit_to(
        sink: AvailabilitySink | None,
        code: str,
        operation: str,
        exc: Exception,
    ) -> None:
        event = AvailabilityEvent(
            alert_type="AVAILABILITY",
            severity="HIGH",
            code=code,
            operation=operation,
            occurred_at_utc=_utc_text(),
            detail=f"{type(exc).__name__}: {exc}",
        )
        LOGGER.error("%s during %s: %s", code, operation, event.detail)
        if sink is not None:
            sink(event)

    def _guard(self, operation: str, work: Callable[[], T]) -> T:
        try:
            return work()
        except Exception as exc:
            self._emit_to(self._availability_sink, "STORAGE_OPERATION_FAILED", operation, exc)
            if isinstance(exc, StorageError):
                raise
            raise StorageUnavailableError(f"storage operation failed: {operation}") from exc

    @staticmethod
    def _queue_audit(
        connection: sqlite3.Connection,
        *,
        record_id: str,
        event_type: str,
        occurred_at_utc: str,
        correlation_id: str,
        payload_json: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_outbox(
                record_id, event_type, occurred_at_utc, correlation_id, payload_json
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO NOTHING
            """,
            (record_id, event_type, occurred_at_utc, correlation_id, payload_json),
        )

    def _enforce_provenance(self, provenance: DataProvenance | list[DataProvenance]) -> None:
        values = provenance if isinstance(provenance, list) else [provenance]
        if self.settings.synthetic_only and any(
            value != DataProvenance.SYNTHETIC for value in values
        ):
            raise StorageUnavailableError("development storage accepts SYNTHETIC provenance only")

    def upsert_user(self, user_id: str, state: UserState) -> None:
        def work() -> None:
            now = _utc_text()
            payload = _json(
                {
                    "schema_version": PROTOCOL_VERSION,
                    "user_id": user_id,
                    "state": _enum_value(state),
                }
            )
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO users(
                        user_id, state, schema_version, created_at_utc, updated_at_utc
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        state = excluded.state,
                        schema_version = excluded.schema_version,
                        updated_at_utc = excluded.updated_at_utc
                    """,
                    (user_id, _enum_value(state), PROTOCOL_VERSION, now, now),
                )
                self._queue_audit(
                    connection,
                    record_id=f"user-state:{user_id}:{now}",
                    event_type="USER_STATE",
                    occurred_at_utc=now,
                    correlation_id=str(uuid.uuid4()),
                    payload_json=payload,
                )
            self.flush_audit()

        self._guard("upsert_user", work)

    def create_session(
        self,
        session_id: str,
        user_id: str,
        entry_auth_evidence: VerificationAnchor,
        *,
        started_at: datetime | None = None,
    ) -> None:
        def work() -> None:
            started = _utc_text(started_at)
            payload = _json(
                {
                    "schema_version": PROTOCOL_VERSION,
                    "session_id": session_id,
                    "user_id": user_id,
                    "entry_auth_evidence": _enum_value(entry_auth_evidence),
                    "started_at_utc": started,
                }
            )
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO sessions(
                        session_id, user_id, entry_auth_evidence, started_at_utc, schema_version
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        user_id,
                        _enum_value(entry_auth_evidence),
                        started,
                        PROTOCOL_VERSION,
                    ),
                )
                self._queue_audit(
                    connection,
                    record_id=f"session-start:{session_id}",
                    event_type="SESSION_STARTED",
                    occurred_at_utc=started,
                    correlation_id=str(uuid.uuid4()),
                    payload_json=payload,
                )
            self.flush_audit()

        self._guard("create_session", work)

    def create_segment(
        self,
        segment_id: str,
        session_id: str,
        started_at_capture_us: int,
        boundary_reason: str,
    ) -> None:
        def work() -> None:
            occurred = _utc_text()
            payload = _json(
                {
                    "schema_version": PROTOCOL_VERSION,
                    "segment_id": segment_id,
                    "session_id": session_id,
                    "started_at_capture_us": started_at_capture_us,
                    "boundary_reason": boundary_reason,
                }
            )
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO segments(
                        segment_id, session_id, started_at_capture_us,
                        boundary_reason, schema_version
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        segment_id,
                        session_id,
                        started_at_capture_us,
                        boundary_reason,
                        PROTOCOL_VERSION,
                    ),
                )
                self._queue_audit(
                    connection,
                    record_id=f"segment-start:{segment_id}",
                    event_type="SEGMENT_STARTED",
                    occurred_at_utc=occurred,
                    correlation_id=str(uuid.uuid4()),
                    payload_json=payload,
                )
            self.flush_audit()

        self._guard("create_segment", work)

    def end_segment(
        self,
        segment_id: str,
        ended_at_capture_us: int,
        reason: str,
    ) -> None:
        def work() -> None:
            occurred = _utc_text()
            payload = _json(
                {
                    "schema_version": PROTOCOL_VERSION,
                    "segment_id": segment_id,
                    "ended_at_capture_us": ended_at_capture_us,
                    "reason": reason,
                }
            )
            with self.database.transaction() as connection:
                updated = connection.execute(
                    """
                    UPDATE segments SET ended_at_capture_us = ?
                    WHERE segment_id = ? AND ended_at_capture_us IS NULL
                    """,
                    (ended_at_capture_us, segment_id),
                )
                if updated.rowcount != 1:
                    raise StorageUnavailableError("segment close references no open segment")
                self._queue_audit(
                    connection,
                    record_id=f"segment-end:{segment_id}",
                    event_type="SEGMENT_ENDED",
                    occurred_at_utc=occurred,
                    correlation_id=segment_id,
                    payload_json=payload,
                )
            self.flush_audit()

        self._guard("end_segment", work)

    def end_session(self, session_id: str, *, ended_at: datetime | None = None) -> None:
        def work() -> None:
            occurred = _utc_text(ended_at)
            payload = _json(
                {
                    "schema_version": PROTOCOL_VERSION,
                    "session_id": session_id,
                    "ended_at_utc": occurred,
                }
            )
            with self.database.transaction() as connection:
                updated = connection.execute(
                    """
                    UPDATE sessions SET ended_at_utc = ?
                    WHERE session_id = ? AND ended_at_utc IS NULL
                    """,
                    (occurred, session_id),
                )
                if updated.rowcount != 1:
                    raise StorageUnavailableError("session close references no open session")
                self._queue_audit(
                    connection,
                    record_id=f"session-end:{session_id}",
                    event_type="SESSION_ENDED",
                    occurred_at_utc=occurred,
                    correlation_id=session_id,
                    payload_json=payload,
                )
            self.flush_audit()

        self._guard("end_session", work)

    def store_feature_window(self, window: FeatureWindow) -> None:
        def work() -> None:
            self._enforce_provenance(window.provenance)
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO feature_windows(
                        window_id, user_id, session_id, segment_id, t_start_us, t_end_us,
                        quality_label, key_event_count, mouse_event_count, collection_day,
                        provenance, keyboard_features_json, mouse_features_json, context_json,
                        schema_version, stored_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        window.window_id,
                        window.user_id,
                        window.session_id,
                        window.segment_id,
                        window.t_start_us,
                        window.t_end_us,
                        _enum_value(window.quality_label),
                        window.key_event_count,
                        window.mouse_event_count,
                        window.collection_day,
                        _enum_value(window.provenance),
                        (
                            None
                            if window.keyboard_features is None
                            else _json(window.keyboard_features)
                        ),
                        None if window.mouse_features is None else _json(window.mouse_features),
                        _json(window.context),
                        window.schema_version,
                        _utc_text(),
                    ),
                )

        self._guard("store_feature_window", work)

    def store_score(self, score: ScoreResult) -> None:
        def work() -> None:
            with self.database.transaction() as connection:
                source = connection.execute(
                    "SELECT provenance FROM feature_windows WHERE window_id = ?", (score.window_id,)
                ).fetchone()
                if source is None:
                    raise StorageUnavailableError("score references an unknown feature window")
                self._enforce_provenance(DataProvenance(source["provenance"]))
                connection.execute(
                    """
                    INSERT INTO scores(
                        user_id, window_id, profile_version, feature_schema_version,
                        model_version, quality_label, score_json, schema_version, stored_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        score.user_id,
                        score.window_id,
                        score.profile_version,
                        score.feature_schema_version,
                        score.model_version,
                        _enum_value(score.quality_label),
                        _json(score),
                        score.schema_version,
                        _utc_text(),
                    ),
                )

        self._guard("store_score", work)

    def store_risk_decision(self, decision: RiskDecision) -> None:
        def work() -> None:
            payload = _json(decision)
            occurred = _utc_text()
            with self.database.transaction() as connection:
                source = connection.execute(
                    "SELECT provenance FROM feature_windows WHERE window_id = ?",
                    (decision.window_id,),
                ).fetchone()
                if source is None:
                    raise StorageUnavailableError("decision references an unknown feature window")
                self._enforce_provenance(DataProvenance(source["provenance"]))
                connection.execute(
                    """
                    INSERT INTO risk_events(
                        decision_id, user_id, session_id, segment_id, window_id, t_decision_us,
                        fused_score, context_confidence, smoothed_score, risk_level, user_state,
                        action, reason_code, threshold_config_version, config_checksum, shadow_mode,
                        enforcement_applied, decision_json, schema_version, stored_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        decision.decision_id,
                        decision.user_id,
                        decision.session_id,
                        decision.segment_id,
                        decision.window_id,
                        decision.t_decision_us,
                        decision.fused_score,
                        decision.context_confidence,
                        decision.smoothed_score,
                        _enum_value(decision.risk_level),
                        _enum_value(decision.user_state),
                        _enum_value(decision.action),
                        decision.reason_code,
                        decision.threshold_config_version,
                        decision.config_checksum,
                        int(decision.shadow_mode),
                        int(decision.enforcement_applied),
                        payload,
                        decision.schema_version,
                        occurred,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO decisions(decision_id, action, enforcement_applied)
                    VALUES (?, ?, ?)
                    """,
                    (
                        decision.decision_id,
                        _enum_value(decision.action),
                        int(decision.enforcement_applied),
                    ),
                )
                self._queue_audit(
                    connection,
                    record_id=f"risk-decision:{decision.decision_id}",
                    event_type="RISK_DECISION",
                    occurred_at_utc=occurred,
                    correlation_id=decision.decision_id,
                    payload_json=payload,
                )
            self.flush_audit()

        self._guard("store_risk_decision", work)

    def record_action_outcome(self, outcome: ActionOutcome) -> None:
        """Attach the real adapter result to a previously audited policy decision."""

        def work() -> None:
            occurred = _utc_text(outcome.occurred_at)
            payload = _json(
                {
                    "decision_id": outcome.decision_id,
                    "requested_action": _enum_value(outcome.requested_action),
                    "status": _enum_value(outcome.status),
                    "code": outcome.code,
                    "occurred_at_utc": occurred,
                }
            )
            with self.database.transaction() as connection:
                updated = connection.execute(
                    """
                    UPDATE decisions
                    SET outcome = ?, outcome_at_utc = ?,
                        enforcement_applied = CASE WHEN ? = 'SUCCEEDED' THEN 1 ELSE 0 END
                    WHERE decision_id = ?
                    """,
                    (outcome.code, occurred, _enum_value(outcome.status), outcome.decision_id),
                )
                if updated.rowcount != 1:
                    raise StorageUnavailableError("action outcome references an unknown decision")
                self._queue_audit(
                    connection,
                    record_id=f"action-outcome:{outcome.decision_id}",
                    event_type="ACTION_OUTCOME",
                    occurred_at_utc=occurred,
                    correlation_id=outcome.decision_id,
                    payload_json=payload,
                )
            self.flush_audit()

        self._guard("record_action_outcome", work)

    def record_verification(self, verification: VerificationRecord) -> None:
        """Persist only independently evidenced A1-A3 anchors and an opaque proof digest."""

        def work() -> None:
            occurred = _utc_text(verification.authenticated_at)
            payload = _json(
                {
                    "anchor_id": verification.anchor_id,
                    "user_id": verification.user_id,
                    "session_id": verification.session_id,
                    "segment_id": verification.segment_id,
                    "anchor_type": _enum_value(verification.anchor_type),
                    "authenticated_at_utc": occurred,
                }
            )
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO verification_anchors(
                        anchor_id, user_id, session_id, segment_id, anchor_type,
                        evidence_reference, authenticated_at_utc, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        verification.anchor_id,
                        verification.user_id,
                        verification.session_id,
                        verification.segment_id,
                        _enum_value(verification.anchor_type),
                        verification.evidence_reference,
                        occurred,
                        PROTOCOL_VERSION,
                    ),
                )
                self._queue_audit(
                    connection,
                    record_id=f"verification:{verification.anchor_id}",
                    event_type="VERIFICATION_ANCHOR",
                    occurred_at_utc=occurred,
                    correlation_id=verification.anchor_id,
                    payload_json=payload,
                )
            self.flush_audit()

        self._guard("record_verification", work)

    def store_alert(self, alert: Alert) -> None:
        def work() -> None:
            payload = _json(alert)
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO alerts(
                        alert_id, alert_type, severity, code, occurred_at_utc,
                        acknowledged, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        alert.alert_id,
                        _enum_value(alert.alert_type),
                        alert.severity,
                        alert.code,
                        alert.occurred_at,
                        int(alert.acknowledged),
                        PROTOCOL_VERSION,
                    ),
                )
                self._queue_audit(
                    connection,
                    record_id=f"alert:{alert.alert_id}",
                    event_type="ALERT",
                    occurred_at_utc=alert.occurred_at,
                    correlation_id=alert.alert_id,
                    payload_json=payload,
                )
            self.flush_audit()

        self._guard("store_alert", work)

    def store_model(self, artifact: ModelArtifact) -> None:
        def work() -> None:
            self._enforce_provenance(artifact.provenance)
            payload = _json(artifact)
            occurred = _utc_text()
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO models(
                        user_id, artifact_version, modality, feature_schema_version,
                        training_range_start, training_range_end, checksum, artifact_json,
                        schema_version, stored_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact.user_id,
                        artifact.artifact_version,
                        artifact.modality,
                        artifact.feature_schema_version,
                        artifact.training_range_start,
                        artifact.training_range_end,
                        artifact.checksum,
                        payload,
                        artifact.schema_version,
                        occurred,
                    ),
                )
                self._queue_audit(
                    connection,
                    record_id=f"model:{artifact.user_id}:{artifact.artifact_version}:{artifact.modality}",
                    event_type="MODEL_REGISTERED",
                    occurred_at_utc=occurred,
                    correlation_id=artifact.artifact_version,
                    payload_json=payload,
                )
            self.flush_audit()

        self._guard("store_model", work)

    def store_update_candidate(self, candidate: UpdateCandidate) -> None:
        def work() -> None:
            payload = _json(candidate)
            occurred = _utc_text()
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO update_candidates(
                        candidate_id, user_id, segment_id, disposition, quarantined_at_utc,
                        audit_revision, candidate_json, schema_version, stored_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(candidate_id) DO UPDATE SET
                        disposition = excluded.disposition,
                        audit_revision = excluded.audit_revision,
                        candidate_json = excluded.candidate_json,
                        stored_at_utc = excluded.stored_at_utc
                    """,
                    (
                        candidate.candidate_id,
                        candidate.user_id,
                        candidate.segment_id,
                        _enum_value(candidate.disposition),
                        candidate.quarantined_at,
                        candidate.audit_revision,
                        payload,
                        candidate.schema_version,
                        occurred,
                    ),
                )
                self._queue_audit(
                    connection,
                    record_id=f"update:{candidate.candidate_id}:{candidate.audit_revision}",
                    event_type="UPDATE_CANDIDATE",
                    occurred_at_utc=occurred,
                    correlation_id=candidate.candidate_id,
                    payload_json=payload,
                )
            self.flush_audit()

        self._guard("store_update_candidate", work)

    def register_app(self, entry: AppRegistryEntry) -> None:
        def work() -> None:
            now = _utc_text()
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO app_registry(
                        app_id, process_name, category, schema_version,
                        created_at_utc, updated_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(app_id) DO UPDATE SET
                        process_name = excluded.process_name,
                        category = excluded.category,
                        schema_version = excluded.schema_version,
                        updated_at_utc = excluded.updated_at_utc
                    """,
                    (
                        entry.app_id,
                        entry.process_name,
                        _enum_value(entry.category),
                        entry.schema_version,
                        now,
                        now,
                    ),
                )

        self._guard("register_app", work)

    def record_metrics(self, component: str, observed_at: datetime, metrics: Metrics) -> None:
        self._store_system_record(component, observed_at, "METRICS", metrics)

    def record_health(self, component: str, observed_at: datetime, health: Health) -> None:
        self._store_system_record(component, observed_at, "HEALTH", health)

    def _store_system_record(
        self,
        component: str,
        observed_at: datetime,
        metric_kind: str,
        value: BaseModel,
    ) -> None:
        def work() -> None:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO system_metrics(
                        component, observed_at_utc, metric_kind, metric_json, schema_version
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        component,
                        _utc_text(observed_at),
                        metric_kind,
                        _json(value),
                        PROTOCOL_VERSION,
                    ),
                )

        self._guard("record_system_metric", work)

    def flush_audit(self) -> int:
        """Deliver committed outbox records idempotently to append-only JSONL."""

        delivered = 0
        try:
            with self.database.connection() as connection:
                pending = connection.execute(
                    """
                    SELECT outbox_id, record_id, event_type, occurred_at_utc,
                           correlation_id, payload_json
                    FROM audit_outbox
                    WHERE dispatched_at_utc IS NULL
                    ORDER BY outbox_id
                    """
                ).fetchall()
            for row in pending:
                self.audit.append(
                    event_type=row["event_type"],
                    occurred_at_utc=row["occurred_at_utc"],
                    correlation_id=row["correlation_id"],
                    payload=json.loads(row["payload_json"]),
                    record_id=row["record_id"],
                )
                with self.database.transaction() as connection:
                    connection.execute(
                        """
                        UPDATE audit_outbox
                        SET dispatched_at_utc = ?, retry_count = retry_count + 1
                        WHERE outbox_id = ? AND dispatched_at_utc IS NULL
                        """,
                        (_utc_text(), row["outbox_id"]),
                    )
                delivered += 1
            return delivered
        except Exception as exc:
            self._emit_to(self._availability_sink, "AUDIT_DELIVERY_FAILED", "flush_audit", exc)
            if isinstance(exc, StorageError):
                raise
            raise StorageUnavailableError("audit delivery failed") from exc

    def run_retention(self, *, now: datetime | None = None) -> RetentionResult:
        def work() -> RetentionResult:
            retention = self.settings.retention
            return run_retention(
                self.database,
                self.audit,
                now=now or _utc_now(),
                feature_window_days=retention.feature_window_days,
                score_days=retention.score_days,
                audit_compress_after_days=retention.audit_compress_after_days,
                audit_delete_after_days=retention.audit_delete_after_days,
            )

        return self._guard("run_retention", work)
