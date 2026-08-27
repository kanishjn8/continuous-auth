"""Bound database and audit growth using the validated C9 retention values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .audit import AuditLog
from .database import SQLiteDatabase


@dataclass(frozen=True)
class RetentionResult:
    status: str
    feature_windows_deleted: int
    scores_deleted: int
    risk_events_deleted: int
    alerts_deleted: int
    system_metrics_deleted: int
    audit_outbox_deleted: int
    audit_files_compressed: int
    audit_files_deleted: int
    completed_at_utc: str


def _as_utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def run_retention(
    database: SQLiteDatabase,
    audit: AuditLog,
    *,
    now: datetime,
    feature_window_days: int,
    score_days: int,
    audit_compress_after_days: int,
    audit_delete_after_days: int,
) -> RetentionResult:
    """Delete eligible aggregate rows transactionally, then rotate audit files."""

    feature_cutoff = _as_utc_text(now - timedelta(days=feature_window_days))
    score_cutoff = _as_utc_text(now - timedelta(days=score_days))
    audit_cutoff = _as_utc_text(now - timedelta(days=audit_delete_after_days))
    with database.transaction() as connection:
        feature_count = connection.execute(
            "DELETE FROM feature_windows WHERE stored_at_utc < ?", (feature_cutoff,)
        ).rowcount
        score_count = connection.execute(
            "DELETE FROM scores WHERE stored_at_utc < ?", (score_cutoff,)
        ).rowcount
        risk_count = connection.execute(
            "DELETE FROM risk_events WHERE stored_at_utc < ?", (score_cutoff,)
        ).rowcount
        alert_count = connection.execute(
            "DELETE FROM alerts WHERE occurred_at_utc < ?", (audit_cutoff,)
        ).rowcount
        metric_count = connection.execute(
            "DELETE FROM system_metrics WHERE observed_at_utc < ?", (score_cutoff,)
        ).rowcount
        outbox_count = connection.execute(
            """
            DELETE FROM audit_outbox
            WHERE dispatched_at_utc IS NOT NULL AND occurred_at_utc < ?
            """,
            (audit_cutoff,),
        ).rowcount
    audit_result = audit.rotate_and_retain(
        now=now,
        compress_after_days=audit_compress_after_days,
        delete_after_days=audit_delete_after_days,
    )
    return RetentionResult(
        status="COMPLETED",
        feature_windows_deleted=feature_count,
        scores_deleted=score_count,
        risk_events_deleted=risk_count,
        alerts_deleted=alert_count,
        system_metrics_deleted=metric_count,
        audit_outbox_deleted=outbox_count,
        audit_files_compressed=audit_result.compressed_files,
        audit_files_deleted=audit_result.deleted_files,
        completed_at_utc=_as_utc_text(now),
    )
