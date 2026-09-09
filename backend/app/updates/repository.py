"""SQLite-backed, audited T-018 candidate and active-profile repository."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from backend.app.storage.errors import StorageUnavailableError
from backend.app.storage.service import StorageService
from protocol.generated.python.contracts import UpdateCandidate

from .manager import ModelProfile, ValidationMetrics, ValidationReport


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _profile_from_row(row: sqlite3.Row) -> ModelProfile:
    values = dict(row)
    validation_data = json.loads(values["validation_json"])
    validation = ValidationReport(
        accepted=bool(validation_data["accepted"]),
        code=str(validation_data["code"]),
        baseline=ValidationMetrics(**validation_data["baseline"]),
        candidate=ValidationMetrics(**validation_data["candidate"]),
        # Profiles written before operating_point existed still load. A JSON
        # null false_acceptance_rate round-trips through ValidationMetrics
        # unchanged and needs no migration: validation_json is free-form TEXT
        # with only a json_valid() check.
        operating_point=str(validation_data.get("operating_point", "")),
    )
    return ModelProfile(
        profile_version=str(values["profile_version"]),
        user_id=str(values["user_id"]),
        keyboard_artifact_version=values["keyboard_artifact_version"],
        mouse_artifact_version=values["mouse_artifact_version"],
        aggregate_checksum=str(values["aggregate_checksum"]),
        validation=validation,
        created_at=_parse_time(str(values["created_at_utc"])),
    )


class SQLiteUpdateRepository:
    def __init__(self, storage: StorageService) -> None:
        self.storage = storage

    def _audit(
        self,
        connection: object,
        *,
        record_id: str,
        event_type: str,
        occurred_at: str,
        payload: dict[str, object],
    ) -> None:
        self.storage._queue_audit(  # noqa: SLF001 - same backend persistence boundary
            connection,  # type: ignore[arg-type]
            record_id=record_id,
            event_type=event_type,
            occurred_at_utc=occurred_at,
            correlation_id=str(uuid.uuid4()),
            payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        )

    def save_candidate(self, candidate: UpdateCandidate) -> None:
        self.storage.store_update_candidate(candidate)

    def candidates_for_user(self, user_id: str) -> tuple[UpdateCandidate, ...]:
        with self.storage.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT candidate_json FROM update_candidates
                WHERE user_id = ? ORDER BY quarantined_at_utc, candidate_id
                """,
                (user_id,),
            ).fetchall()
        return tuple(UpdateCandidate.model_validate_json(row["candidate_json"]) for row in rows)

    def schedule_due(self, user_id: str, now: datetime, cadence: timedelta) -> bool:
        with self.storage.database.connection() as connection:
            row = connection.execute(
                """
                SELECT scheduled_for_utc FROM update_runs
                WHERE user_id = ? AND status = 'ACTIVATED'
                ORDER BY scheduled_for_utc DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        return row is None or now.astimezone(UTC) >= _parse_time(row["scheduled_for_utc"]) + cadence

    def begin_run(self, run_id: str, user_id: str, scheduled_for: datetime) -> None:
        occurred = _iso(datetime.now(UTC))
        with self.storage.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO update_runs(
                    run_id, user_id, scheduled_for_utc, started_at_utc, status, report_json
                ) VALUES (?, ?, ?, ?, 'RUNNING', '{}')
                """,
                (run_id, user_id, _iso(scheduled_for), occurred),
            )
            self._audit(
                connection,
                record_id=f"update-run-start:{run_id}",
                event_type="UPDATE_RUN_STARTED",
                occurred_at=occurred,
                payload={"run_id": run_id, "user_id": user_id},
            )
        self.storage.flush_audit()

    def finish_run(self, run_id: str, status: str, report: dict[str, object]) -> None:
        occurred = _iso(datetime.now(UTC))
        payload = json.dumps(report, sort_keys=True, separators=(",", ":"), default=asdict)
        with self.storage.database.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE update_runs
                SET completed_at_utc = ?, status = ?, report_json = ?
                WHERE run_id = ? AND status = 'RUNNING'
                """,
                (occurred, status, payload, run_id),
            )
            if updated.rowcount != 1:
                raise StorageUnavailableError("update run is missing or already completed")
            self._audit(
                connection,
                record_id=f"update-run-finish:{run_id}",
                event_type="UPDATE_RUN_FINISHED",
                occurred_at=occurred,
                payload={"run_id": run_id, "status": status},
            )
        self.storage.flush_audit()

    def activate_profile(self, profile: ModelProfile, retained_versions: int) -> None:
        occurred = _iso(datetime.now(UTC))
        validation_json = json.dumps(
            asdict(profile.validation), sort_keys=True, separators=(",", ":")
        )
        with self.storage.database.transaction() as connection:
            connection.execute(
                """
                UPDATE model_profiles
                SET status = 'RETAINED', retired_at_utc = ?
                WHERE user_id = ? AND status = 'ACTIVE'
                """,
                (occurred, profile.user_id),
            )
            connection.execute(
                """
                INSERT INTO model_profiles(
                    profile_version, user_id, keyboard_artifact_version,
                    mouse_artifact_version, aggregate_checksum, validation_json,
                    status, created_at_utc, activated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
                """,
                (
                    profile.profile_version,
                    profile.user_id,
                    profile.keyboard_artifact_version,
                    profile.mouse_artifact_version,
                    profile.aggregate_checksum,
                    validation_json,
                    _iso(profile.created_at),
                    occurred,
                ),
            )
            retained = connection.execute(
                """
                SELECT profile_version FROM model_profiles
                WHERE user_id = ? AND status = 'RETAINED'
                ORDER BY COALESCE(retired_at_utc, created_at_utc) DESC
                """,
                (profile.user_id,),
            ).fetchall()
            for row in retained[retained_versions:]:
                connection.execute(
                    "DELETE FROM model_profiles WHERE profile_version = ? AND status = 'RETAINED'",
                    (row["profile_version"],),
                )
            self._audit(
                connection,
                record_id=f"profile-activation:{profile.profile_version}",
                event_type="MODEL_PROFILE_ACTIVATED",
                occurred_at=occurred,
                payload={
                    "user_id": profile.user_id,
                    "profile_version": profile.profile_version,
                    "aggregate_checksum": profile.aggregate_checksum,
                },
            )
        self.storage.flush_audit()

    def rollback(self, user_id: str, occurred_at: datetime) -> ModelProfile:
        occurred = _iso(occurred_at)
        with self.storage.database.transaction() as connection:
            active = connection.execute(
                "SELECT * FROM model_profiles WHERE user_id = ? AND status = 'ACTIVE'",
                (user_id,),
            ).fetchone()
            retained = connection.execute(
                """
                SELECT * FROM model_profiles
                WHERE user_id = ? AND status = 'RETAINED'
                ORDER BY COALESCE(retired_at_utc, created_at_utc) DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if active is None or retained is None:
                raise StorageUnavailableError("rollback requires active and retained profiles")
            connection.execute(
                """
                UPDATE model_profiles SET status = 'RETAINED', retired_at_utc = ?
                WHERE profile_version = ?
                """,
                (occurred, active["profile_version"]),
            )
            connection.execute(
                """
                UPDATE model_profiles
                SET status = 'ACTIVE', activated_at_utc = ?, retired_at_utc = NULL
                WHERE profile_version = ?
                """,
                (occurred, retained["profile_version"]),
            )
            self._audit(
                connection,
                record_id=f"profile-rollback:{user_id}:{occurred}",
                event_type="MODEL_PROFILE_ROLLED_BACK",
                occurred_at=occurred,
                payload={
                    "user_id": user_id,
                    "from_profile_version": active["profile_version"],
                    "to_profile_version": retained["profile_version"],
                },
            )
        self.storage.flush_audit()
        return _profile_from_row(retained)
