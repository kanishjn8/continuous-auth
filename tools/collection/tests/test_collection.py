from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from protocol.generated.python.contracts import DataProvenance, WindowQuality
from tools.collection.config import load_collection_settings
from tools.collection.eligibility import ConsentRecord, EnrollmentRecord, eligibility_reasons
from tools.collection.freeze import FreezeError, build_freeze, verify_freeze
from tools.collection.health import WindowSummary, build_health_report


def _administration() -> tuple[ConsentRecord, EnrollmentRecord]:
    enrolled = datetime(2026, 1, 1, tzinfo=UTC)
    return (
        ConsentRecord("participant_001", "approved-v1", enrolled),
        EnrollmentRecord("participant_001", enrolled, "collector-v1", "1.0.0"),
    )


def _windows(days: int = 5) -> list[WindowSummary]:
    started = datetime(2026, 1, 2, tzinfo=UTC)
    return [
        WindowSummary(
            window_id=f"window-{index}",
            participant_id="participant_001",
            collection_day=(started + timedelta(days=index)).date().isoformat(),
            observed_at=started + timedelta(days=index),
            quality=WindowQuality.FULL,
            provenance=DataProvenance.PILOT,
            key_event_count=20,
            mouse_event_count=20,
            device_generation=0,
            aggregate_checksum=f"{index:064x}",
        )
        for index in range(days)
    ]


def test_consent_and_provenance_are_mandatory() -> None:
    settings = load_collection_settings(Path("config/collection.development.yaml"))
    consent, enrollment = _administration()
    observed = datetime(2026, 1, 2, tzinfo=UTC)
    assert not eligibility_reasons(
        participant_id=consent.participant_id,
        provenance=DataProvenance.PILOT,
        observed_at=observed,
        consent=consent,
        enrollment=enrollment,
        settings=settings,
    )
    assert "MISSING_CONSENT" in eligibility_reasons(
        participant_id=consent.participant_id,
        provenance=DataProvenance.PILOT,
        observed_at=observed,
        consent=None,
        enrollment=enrollment,
        settings=settings,
    )
    assert "INELIGIBLE_PROVENANCE" in eligibility_reasons(
        participant_id=consent.participant_id,
        provenance=DataProvenance.SYNTHETIC,
        observed_at=observed,
        consent=consent,
        enrollment=enrollment,
        settings=settings,
    )


def test_health_surfaces_shortfalls_and_device_change() -> None:
    settings = load_collection_settings(Path("config/collection.development.yaml"))
    consent, enrollment = _administration()
    windows = _windows(3)
    windows[1] = replace(windows[1], quality=WindowQuality.KBD_ONLY, device_generation=1)
    report = build_health_report(
        windows,
        consents={consent.participant_id: consent},
        enrollments={enrollment.participant_id: enrollment},
        settings=settings,
        generated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    participant = report.participants[0]
    assert participant.device_changes == 2
    assert "DEVICE_CONFIGURATION_CHANGED" in participant.shortfalls
    assert "COLLECTION_DAYS_BELOW_TARGET" in participant.shortfalls
    assert participant.ineligible_windows == 0


def test_freeze_is_day_disjoint_write_once_and_checksum_verified(tmp_path: Path) -> None:
    settings = load_collection_settings(Path("config/collection.development.yaml"))
    consent, enrollment = _administration()
    windows = _windows()
    destination = tmp_path / "manifest.json"
    document = build_freeze(
        windows,
        destination=destination,
        version="pilot-v1",
        consents={consent.participant_id: consent},
        enrollments={enrollment.participant_id: enrollment},
        settings=settings,
        frozen_at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    assert len(document["manifest_checksum"]) == 64
    counts = verify_freeze(destination, windows)
    assert set(counts) == {"EVALUATION", "TRAIN", "VALIDATION"}
    with pytest.raises(FreezeError, match="already exists"):
        build_freeze(
            windows,
            destination=destination,
            version="pilot-v1",
            consents={consent.participant_id: consent},
            enrollments={enrollment.participant_id: enrollment},
            settings=settings,
        )
    changed = [replace(windows[0], aggregate_checksum="f" * 64), *windows[1:]]
    with pytest.raises(FreezeError, match="checksums changed"):
        verify_freeze(destination, changed)


def test_freeze_refuses_missing_consent(tmp_path: Path) -> None:
    settings = load_collection_settings(Path("config/collection.development.yaml"))
    _, enrollment = _administration()
    with pytest.raises(FreezeError, match="MISSING_CONSENT"):
        build_freeze(
            _windows(),
            destination=tmp_path / "manifest.json",
            version="pilot-v1",
            consents={},
            enrollments={enrollment.participant_id: enrollment},
            settings=settings,
        )


def test_freeze_minimum_three_days_keeps_all_partitions_non_empty(tmp_path: Path) -> None:
    settings = load_collection_settings(Path("config/collection.development.yaml"))
    consent, enrollment = _administration()
    windows = _windows(3)
    destination = tmp_path / "minimum.json"
    build_freeze(
        windows,
        destination=destination,
        version="minimum-v1",
        consents={consent.participant_id: consent},
        enrollments={enrollment.participant_id: enrollment},
        settings=settings,
    )
    assert verify_freeze(destination, windows) == {
        "EVALUATION": 1,
        "TRAIN": 1,
        "VALIDATION": 1,
    }
