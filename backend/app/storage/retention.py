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
    with database.transaction() as connection:
        feature_count = connection.execute(
            "DELETE FROM feature_windows WHERE stored_at_utc < ?", (feature_cutoff,)
        ).rowcount
        score_count = connection.execute(
            "DELETE FROM scores WHERE stored_at_utc < ?", (score_cutoff,)
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
        audit_files_compressed=audit_result.compressed_files,
        audit_files_deleted=audit_result.deleted_files,
        completed_at_utc=_as_utc_text(now),
    )
