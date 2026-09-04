"""ADR-013: a user's first profile is admitted on consent and corpus evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ml.features.schema import (
    AppCategory,
    ContextBlock,
    DeviceClass,
    FEATURE_SCHEMA_VERSION,
    FeatureWindow,
    Provenance,
    QualityLabel,
)
from ml.training.enrollment import (
    EnrollmentAdmission,
    EnrollmentAdmissionError,
    require_enrollment_admission,
)
from tools.collection.config import load_collection_settings
from tools.collection.eligibility import ConsentRecord, EnrollmentRecord

CONSENTED = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def make_window(
    *,
    user_id: str,
    window_id: str,
    collection_day: str,
    segment_id: str,
    provenance: Provenance,
    session_id: str | None = None,
) -> FeatureWindow:
    """Local helper: `ml.tests.conftest` has no `make_window`, so this builds
    a minimal valid FeatureWindow (INSUFFICIENT_DATA quality, no feature
    blocks) with the identity/provenance fields the enrollment gate cares
    about left settable. The gate never reads keyboard/mouse features.
    """

    return FeatureWindow(
        schema_version=FEATURE_SCHEMA_VERSION,
        user_id=user_id,
        session_id=session_id or f"{user_id}-session",
        segment_id=segment_id,
        window_id=window_id,
        t_start_us=0,
        t_end_us=1,
        quality_label=QualityLabel.INSUFFICIENT_DATA,
        key_event_count=0,
        mouse_event_count=0,
        collection_day=collection_day,
        provenance=provenance,
        keyboard_features=None,
        mouse_features=None,
        context=ContextBlock(
            dominant_category=AppCategory.PRODUCTIVITY,
            category_fractions={"PRODUCTIVITY": 1.0},
            app_switch_rate=0.0,
            device_class=DeviceClass.INTERNAL_KEYBOARD,
            app_shares=[],
        ),
    )


def _settings():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    return load_collection_settings(root / "config/collection.pilot.yaml")


def _windows(count: int = 30, days: int = 3, user_id: str = "participant-01"):
    return [
        make_window(
            user_id=user_id,
            window_id=f"w{index}",
            segment_id=f"seg{index % 5}",
            collection_day=f"2026-09-0{1 + index % days}",
            provenance=Provenance.PILOT,
        )
        for index in range(count)
    ]


def _admission(windows, **overrides):
    defaults = dict(
        settings=_settings(),
        consent=ConsentRecord(
            participant_id="participant-01",
            protocol_revision="v1",
            consented_at=CONSENTED,
        ),
        enrollment=EnrollmentRecord(
            participant_id="participant-01",
            enrolled_at=CONSENTED,
            collector_version="1.0.0",
            protocol_version="1.0.0",
        ),
        manifest_window_ids=frozenset(w.window_id for w in windows),
        observed_at_by_window={
            w.window_id: CONSENTED + timedelta(hours=index)
            for index, w in enumerate(windows)
        },
        min_windows=20,
        min_distinct_days=3,
        user_has_active_profile=False,
    )
    defaults.update(overrides)
    return EnrollmentAdmission(**defaults)


def test_complete_evidence_is_admitted() -> None:
    windows = _windows()
    require_enrollment_admission("participant-01", windows, _admission(windows))


def test_a_window_from_another_user_is_refused() -> None:
    windows = _windows() + _windows(count=1, user_id="participant-02")
    with pytest.raises(EnrollmentAdmissionError, match="FOREIGN_USER_WINDOW"):
        require_enrollment_admission("participant-01", windows, _admission(windows))


def test_synthetic_provenance_is_refused() -> None:
    windows = _windows()
    windows.append(
        make_window(
            user_id="participant-01",
            window_id="w-synth",
            segment_id="seg0",
            collection_day="2026-09-01",
            provenance=Provenance.SYNTHETIC,
        )
    )
    with pytest.raises(EnrollmentAdmissionError, match="INELIGIBLE_PROVENANCE"):
        require_enrollment_admission("participant-01", windows, _admission(windows))


def test_missing_consent_is_refused() -> None:
    windows = _windows()
    with pytest.raises(EnrollmentAdmissionError, match="MISSING_CONSENT"):
        require_enrollment_admission(
            "participant-01", windows, _admission(windows, consent=None)
        )


def test_withdrawn_consent_is_refused() -> None:
    windows = _windows()
    withdrawn = ConsentRecord(
        participant_id="participant-01",
        protocol_revision="v1",
        consented_at=CONSENTED,
        withdrawn_at=CONSENTED + timedelta(minutes=1),
    )
    with pytest.raises(EnrollmentAdmissionError, match="CONSENT_NOT_ACTIVE"):
        require_enrollment_admission(
            "participant-01", windows, _admission(windows, consent=withdrawn)
        )


def test_missing_enrollment_is_refused() -> None:
    windows = _windows()
    with pytest.raises(EnrollmentAdmissionError, match="MISSING_ENROLLMENT"):
        require_enrollment_admission(
            "participant-01", windows, _admission(windows, enrollment=None)
        )


def test_a_window_outside_the_frozen_corpus_is_refused() -> None:
    windows = _windows()
    partial = frozenset(w.window_id for w in windows[:-1])
    with pytest.raises(EnrollmentAdmissionError, match="WINDOW_NOT_IN_FROZEN_CORPUS"):
        require_enrollment_admission(
            "participant-01", windows, _admission(windows, manifest_window_ids=partial)
        )


def test_a_window_without_an_observation_time_is_refused() -> None:
    windows = _windows()
    partial = {w.window_id: CONSENTED for w in windows[:-1]}
    with pytest.raises(EnrollmentAdmissionError, match="MISSING_OBSERVATION_TIME"):
        require_enrollment_admission(
            "participant-01", windows, _admission(windows, observed_at_by_window=partial)
        )


def test_too_few_windows_is_refused() -> None:
    windows = _windows(count=5)
    with pytest.raises(EnrollmentAdmissionError, match="INSUFFICIENT_WINDOWS"):
        require_enrollment_admission("participant-01", windows, _admission(windows))


def test_too_few_distinct_days_is_refused() -> None:
    windows = _windows(days=1)
    with pytest.raises(EnrollmentAdmissionError, match="INSUFFICIENT_DISTINCT_DAYS"):
        require_enrollment_admission("participant-01", windows, _admission(windows))


def test_a_user_with_an_active_profile_is_refused() -> None:
    """Once a profile exists, only the Model Update Manager may add data."""

    windows = _windows()
    with pytest.raises(EnrollmentAdmissionError, match="PROFILE_ALREADY_ACTIVE"):
        require_enrollment_admission(
            "participant-01", windows, _admission(windows, user_has_active_profile=True)
        )


def test_adr_013_is_recorded_in_the_plan() -> None:
    """The gate and the decision that authorises it ship together.

    ``PLAN.md`` is deliberately gitignored (.gitignore:83, team decision in
    commit 9ebaf87) alongside START_IMPLEMENTATION.md and
    TASK_DELEGATION.md, so it will not exist on a clean checkout or anyone
    else's machine. ADR-013 is written there on disk for the team (the
    document this project actually reads), but its durable, version-
    controlled record is the approved spec at
    docs/superpowers/specs/2026-09-03-pilot-default-workflow-design.md
    Section 5.4. This test asserts against PLAN.md when it is present and
    skips -- rather than failing -- when it is not, so the skip here is
    deliberate, not an oversight.
    """

    from pathlib import Path

    plan_path = Path(__file__).resolve().parents[2] / "PLAN.md"
    if not plan_path.is_file():
        pytest.skip(
            "PLAN.md is deliberately gitignored (.gitignore:83); ADR-013 is "
            "recorded in docs/superpowers/specs/2026-09-03-pilot-default-"
            "workflow-design.md Section 5.4"
        )
    plan = plan_path.read_text(encoding="utf-8")
    assert "ADR-013" in plan
    assert "Enrollment Admission Is Distinct From Update Promotion" in plan


def test_promotion_gate_source_is_unmodified() -> None:
    """ADR-013 adds a boundary; it does not relax the existing one."""

    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "ml" / "training" / "gate.py"
    ).read_text(encoding="utf-8")
    for gate in ("g1_risk", "g2_verification", "g3_volume", "g4_continuity",
                 "g5_quarantine", "g6_schedule"):
        assert gate in source
    assert "CandidateDisposition.PROMOTED" in source
