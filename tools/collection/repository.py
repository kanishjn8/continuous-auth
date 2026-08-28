"""Read aggregate collection metadata from local SQLite storage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from protocol.generated.python.contracts import DataProvenance, InputDeviceClass, WindowQuality

from .eligibility import ConsentRecord, EnrollmentRecord
from .health import WindowSummary


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("stored collection timestamp has no timezone")
    return parsed.astimezone(UTC)


def _digest(row: sqlite3.Row) -> str:
    fields = {
        key: row[key]
        for key in (
            "window_id",
            "user_id",
            "session_id",
            "segment_id",
            "t_start_us",
            "t_end_us",
            "quality_label",
            "key_event_count",
            "mouse_event_count",
            "collection_day",
            "provenance",
            "keyboard_features_json",
            "mouse_features_json",
            "context_json",
            "schema_version",
        )
    }
    payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def load_window_summaries(database_path: Path) -> list[WindowSummary]:
    """Return aggregate-only summaries and a device-change generation per participant."""

    if not database_path.is_file():
        raise FileNotFoundError("collection database does not exist")
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT window_id, user_id, session_id, segment_id, t_start_us, t_end_us,
                   quality_label, key_event_count, mouse_event_count, collection_day,
                   provenance, keyboard_features_json, mouse_features_json, context_json,
                   schema_version, stored_at_utc
            FROM feature_windows
            ORDER BY user_id, stored_at_utc, window_id
            """
        ).fetchall()
    finally:
        connection.close()

    generations: dict[str, int] = defaultdict(int)
    prior_device: dict[str, InputDeviceClass] = {}
    result: list[WindowSummary] = []
    for row in rows:
        context: Any = json.loads(str(row["context_json"]))
        device = InputDeviceClass(context["device_class"])
        participant = str(row["user_id"])
        if participant in prior_device and prior_device[participant] != device:
            generations[participant] += 1
        prior_device[participant] = device
        result.append(
            WindowSummary(
                window_id=str(row["window_id"]),
                participant_id=participant,
                collection_day=str(row["collection_day"]),
                observed_at=_utc(str(row["stored_at_utc"])),
                quality=WindowQuality(str(row["quality_label"])),
                provenance=DataProvenance(str(row["provenance"])),
                key_event_count=int(row["key_event_count"]),
                mouse_event_count=int(row["mouse_event_count"]),
                device_generation=generations[participant],
                aggregate_checksum=_digest(row),
            )
        )
    return result


def load_administration_records(
    path: Path,
) -> tuple[dict[str, ConsentRecord], dict[str, EnrollmentRecord]]:
    """Load local human-entered records; the file belongs below ignored data/."""

    try:
        document: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"collection administration records rejected: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("collection administration records must be an object")
    consents: dict[str, ConsentRecord] = {}
    enrollments: dict[str, EnrollmentRecord] = {}
    for value in document.get("consents", []):
        consent = ConsentRecord(
            participant_id=value["participant_id"],
            protocol_revision=value["protocol_revision"],
            consented_at=_utc(value["consented_at"]),
            withdrawn_at=_utc(value["withdrawn_at"]) if value.get("withdrawn_at") else None,
        )
        if consent.participant_id in consents:
            raise ValueError("duplicate consent participant_id")
        consents[consent.participant_id] = consent
    for value in document.get("enrollments", []):
        enrollment = EnrollmentRecord(
            participant_id=value["participant_id"],
            enrolled_at=_utc(value["enrolled_at"]),
            collector_version=value["collector_version"],
            protocol_version=value["protocol_version"],
        )
        if enrollment.participant_id in enrollments:
            raise ValueError("duplicate enrollment participant_id")
        enrollments[enrollment.participant_id] = enrollment
    return consents, enrollments
