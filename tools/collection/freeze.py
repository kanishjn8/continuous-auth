"""Create and verify immutable, checksum-versioned day-disjoint manifests."""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import CollectionSettings
from .eligibility import ConsentRecord, EnrollmentRecord, eligibility_reasons
from .health import WindowSummary


class FreezeError(RuntimeError):
    """The corpus cannot be frozen or no longer matches its immutable manifest."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _window_digest(window: WindowSummary) -> str:
    value = asdict(window)
    value["observed_at"] = window.observed_at.astimezone(UTC).isoformat()
    value["quality"] = window.quality.value
    value["provenance"] = window.provenance.value
    return hashlib.sha256(_canonical(value)).hexdigest()


def _partition_days(days: list[str], settings: CollectionSettings) -> dict[str, str]:
    if len(days) < settings.min_distinct_days:
        raise FreezeError("participant has fewer than the configured minimum distinct days")
    train_end = max(1, round(len(days) * settings.training_fraction))
    validation_count = max(1, round(len(days) * settings.validation_fraction))
    validation_end = min(len(days) - 1, train_end + validation_count)
    if train_end >= validation_end or validation_end >= len(days):
        raise FreezeError(
            "configured split fractions cannot produce three non-empty day partitions"
        )
    return {
        day: (
            "TRAIN"
            if index < train_end
            else "VALIDATION" if index < validation_end else "EVALUATION"
        )
        for index, day in enumerate(days)
    }


def _manifest_document(
    windows: list[WindowSummary],
    *,
    version: str,
    consents: dict[str, ConsentRecord],
    enrollments: dict[str, EnrollmentRecord],
    settings: CollectionSettings,
    frozen_at: datetime,
) -> dict[str, Any]:
    if not version or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for character in version
    ):
        raise FreezeError(
            "freeze version must contain only letters, numbers, hyphen, and underscore"
        )
    grouped: dict[str, list[WindowSummary]] = defaultdict(list)
    for window in windows:
        reasons = eligibility_reasons(
            participant_id=window.participant_id,
            provenance=window.provenance,
            observed_at=window.observed_at,
            consent=consents.get(window.participant_id),
            enrollment=enrollments.get(window.participant_id),
            settings=settings,
        )
        if reasons:
            raise FreezeError(f"window {window.window_id} is ineligible: {','.join(reasons)}")
        grouped[window.participant_id].append(window)
    if not grouped:
        raise FreezeError("no eligible windows were supplied")

    records: list[dict[str, Any]] = []
    assignments: dict[str, dict[str, str]] = {}
    for participant_id in sorted(grouped):
        days = sorted({window.collection_day for window in grouped[participant_id]})
        split_by_day = _partition_days(days, settings)
        assignments[participant_id] = split_by_day
        for window in sorted(grouped[participant_id], key=lambda value: value.window_id):
            records.append(
                {
                    "window_id": window.window_id,
                    "participant_id": participant_id,
                    "collection_day": window.collection_day,
                    "partition": split_by_day[window.collection_day],
                    "record_checksum": _window_digest(window),
                    "provenance": window.provenance.value,
                    "quality": window.quality.value,
                }
            )
    base = {
        "manifest_schema": "continuous-auth-freeze-v1",
        "version": version,
        "frozen_at": frozen_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "config_version": settings.config_version,
        "day_assignments": assignments,
        "records": records,
    }
    return {**base, "manifest_checksum": hashlib.sha256(_canonical(base)).hexdigest()}


def build_freeze(
    windows: list[WindowSummary],
    *,
    destination: Path,
    version: str,
    consents: dict[str, ConsentRecord],
    enrollments: dict[str, EnrollmentRecord],
    settings: CollectionSettings,
    frozen_at: datetime | None = None,
) -> dict[str, Any]:
    """Write a new manifest exactly once and make it read-only."""

    if destination.exists():
        raise FreezeError("freeze destination already exists; frozen versions cannot be mutated")
    document = _manifest_document(
        windows,
        version=version,
        consents=consents,
        enrollments=enrollments,
        settings=settings,
        frozen_at=frozen_at or datetime.now(UTC),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise FreezeError(f"freeze manifest could not be written: {exc}") from exc
    return document


def verify_freeze(manifest_path: Path, windows: list[WindowSummary]) -> dict[str, int]:
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FreezeError(f"freeze manifest could not be read: {exc}") from exc
    checksum = document.pop("manifest_checksum", None)
    if checksum != hashlib.sha256(_canonical(document)).hexdigest():
        raise FreezeError("freeze manifest checksum does not match")
    current = {window.window_id: _window_digest(window) for window in windows}
    records = document.get("records")
    if not isinstance(records, list):
        raise FreezeError("freeze manifest records are invalid")
    expected = {record["window_id"]: record["record_checksum"] for record in records}
    if set(expected) - set(current):
        raise FreezeError("one or more frozen windows are missing")
    if any(current[window_id] != digest for window_id, digest in expected.items()):
        raise FreezeError("one or more frozen window checksums changed")
    partitions = {record["partition"] for record in records}
    if partitions != {"TRAIN", "VALIDATION", "EVALUATION"}:
        raise FreezeError("freeze manifest does not contain all required partitions")
    return {
        partition: sum(record["partition"] == partition for record in records)
        for partition in sorted(partitions)
    }
