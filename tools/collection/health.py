"""Aggregate collection coverage without exposing identity feature vectors."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from protocol.generated.python.contracts import DataProvenance, WindowQuality

from .config import CollectionSettings
from .eligibility import ConsentRecord, EnrollmentRecord, eligibility_reasons


@dataclass(frozen=True)
class WindowSummary:
    window_id: str
    participant_id: str
    collection_day: str
    observed_at: datetime
    quality: WindowQuality
    provenance: DataProvenance
    key_event_count: int
    mouse_event_count: int
    device_generation: int = 0
    aggregate_checksum: str = ""

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("window observed_at must include a timezone")
        if self.key_event_count < 0 or self.mouse_event_count < 0:
            raise ValueError("event counts cannot be negative")
        if self.aggregate_checksum and (
            len(self.aggregate_checksum) != 64
            or any(character not in "0123456789abcdef" for character in self.aggregate_checksum)
        ):
            raise ValueError("aggregate_checksum must be a lowercase SHA-256 digest")
        datetime.fromisoformat(self.collection_day)


@dataclass(frozen=True)
class ParticipantHealth:
    participant_id: str
    distinct_days: int
    total_windows: int
    quality_counts: dict[str, int]
    keyboard_window_fraction: float
    mouse_window_fraction: float
    full_modality_fraction: float
    largest_gap_hours: float
    device_changes: int
    ineligible_windows: int
    shortfalls: tuple[str, ...]


@dataclass(frozen=True)
class CollectionHealthReport:
    generated_at: str
    config_version: str
    participants: tuple[ParticipantHealth, ...]
    totals: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _gap_hours(windows: list[WindowSummary]) -> float:
    ordered = sorted(window.observed_at.astimezone(UTC) for window in windows)
    if len(ordered) < 2:
        return 0.0
    return max(
        (right - left).total_seconds() / 3600
        for left, right in zip(ordered, ordered[1:], strict=False)
    )


def build_health_report(
    windows: list[WindowSummary],
    *,
    consents: dict[str, ConsentRecord],
    enrollments: dict[str, EnrollmentRecord],
    settings: CollectionSettings,
    generated_at: datetime | None = None,
) -> CollectionHealthReport:
    grouped: dict[str, list[WindowSummary]] = defaultdict(list)
    for window in windows:
        grouped[window.participant_id].append(window)

    participant_reports: list[ParticipantHealth] = []
    total_ineligible = 0
    for participant_id in sorted(grouped):
        values = grouped[participant_id]
        quality = Counter(window.quality.value for window in values)
        ineligible = sum(
            bool(
                eligibility_reasons(
                    participant_id=participant_id,
                    provenance=window.provenance,
                    observed_at=window.observed_at,
                    consent=consents.get(participant_id),
                    enrollment=enrollments.get(participant_id),
                    settings=settings,
                )
            )
            for window in values
        )
        total_ineligible += ineligible
        total = len(values)
        keyboard_windows = quality[WindowQuality.FULL.value] + quality[WindowQuality.KBD_ONLY.value]
        mouse_windows = quality[WindowQuality.FULL.value] + quality[WindowQuality.MOUSE_ONLY.value]
        full_fraction = quality[WindowQuality.FULL.value] / total
        days = len({window.collection_day for window in values})
        per_day = Counter(window.collection_day for window in values)
        largest_gap = _gap_hours(values)
        device_changes = sum(
            left.device_generation != right.device_generation
            for left, right in zip(
                sorted(values, key=lambda value: value.observed_at),
                sorted(values, key=lambda value: value.observed_at)[1:],
                strict=False,
            )
        )
        shortfalls: list[str] = []
        if days < settings.target_collection_days:
            shortfalls.append("COLLECTION_DAYS_BELOW_TARGET")
        if any(count < settings.min_windows_per_day for count in per_day.values()):
            shortfalls.append("WINDOWS_PER_DAY_BELOW_TARGET")
        if full_fraction < settings.min_full_modality_fraction:
            shortfalls.append("FULL_MODALITY_BALANCE_BELOW_TARGET")
        if largest_gap > settings.max_observed_gap_hours:
            shortfalls.append("COLLECTION_GAP_ABOVE_LIMIT")
        if device_changes:
            shortfalls.append("DEVICE_CONFIGURATION_CHANGED")
        if ineligible:
            shortfalls.append("INELIGIBLE_WINDOWS_PRESENT")
        participant_reports.append(
            ParticipantHealth(
                participant_id=participant_id,
                distinct_days=days,
                total_windows=total,
                quality_counts=dict(sorted(quality.items())),
                keyboard_window_fraction=keyboard_windows / total,
                mouse_window_fraction=mouse_windows / total,
                full_modality_fraction=full_fraction,
                largest_gap_hours=largest_gap,
                device_changes=device_changes,
                ineligible_windows=ineligible,
                shortfalls=tuple(shortfalls),
            )
        )
    now = (generated_at or datetime.now(UTC)).astimezone(UTC)
    return CollectionHealthReport(
        generated_at=now.isoformat().replace("+00:00", "Z"),
        config_version=settings.config_version,
        participants=tuple(participant_reports),
        totals={
            "participants": len(participant_reports),
            "windows": len(windows),
            "ineligible_windows": total_ineligible,
        },
    )
