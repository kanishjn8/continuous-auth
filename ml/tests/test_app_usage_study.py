from __future__ import annotations

import re
from pathlib import Path

import pytest

from ml.experiments.app_usage_study import (
    MIN_DAYS_PER_USER,
    MIN_SESSIONS_PER_USER,
    MIN_USERS,
    USAGE_FEATURE_NAMES,
    assess_corpus,
    assess_windows,
    build_usage_sample,
    build_usage_samples,
    data_requirement_summary,
    run_study,
)
from ml.features.extractor import extract_windows
from ml.features.schema import AppCategory, ContextEvent, KeyClass, Provenance
from ml.tests.conftest import kbd, mouse_move

#: The prohibition this study must never quietly break (AGENTS.md constraint 4,
#: guardrail G04_TEMPORAL_IDENTITY).
_CLOCK_POSITION = re.compile(r"time_of_day|day_of_week|hour_of_day|weekday|hour|clock", re.I)


def _session_windows(
    ml_config,
    *,
    user_id: str,
    session_id: str,
    collection_day: str,
    apps: list[tuple[int, AppCategory]],
    span_us: int = 4_000_000,
):
    """One session's worth of windows rotating through ``apps``."""

    step = max(span_us // 120, 1)
    kbd_events = [
        kbd(i, i * step, KeyClass.ALPHA_L_HOME, "KEY_DOWN") for i in range(span_us // step)
    ]
    mouse_events = [mouse_move(i, i * step, 100 + i, 100 + i) for i in range(span_us // step)]
    slice_us = span_us // len(apps)
    context = [
        ContextEvent(t_capture_us=index * slice_us, app_id=app_id, category=category, seq=index)
        for index, (app_id, category) in enumerate(apps)
    ]
    return extract_windows(
        kbd_events,
        mouse_events,
        context,
        user_id=user_id,
        session_id=session_id,
        segment_id=f"{session_id}-seg",
        collection_day=collection_day,
        provenance=Provenance.SYNTHETIC,
        config=ml_config,
    )


# ----------------------------------------------------------------------
# Privacy / scope invariants
# ----------------------------------------------------------------------
def test_no_usage_feature_encodes_clock_position() -> None:
    for name in USAGE_FEATURE_NAMES:
        assert not _CLOCK_POSITION.search(name), name


def test_usage_features_are_unique_and_non_empty() -> None:
    assert len(USAGE_FEATURE_NAMES) == len(set(USAGE_FEATURE_NAMES))
    assert USAGE_FEATURE_NAMES


def test_study_module_does_not_import_the_risk_engine() -> None:
    """Problem 1 must stay isolated from the live decision path."""

    source = Path("ml/experiments/app_usage_study.py").read_text(encoding="utf-8")
    assert "backend.app.risk" not in source
    assert "RiskEngine" not in source


# ----------------------------------------------------------------------
# Corpus sufficiency
# ----------------------------------------------------------------------
def test_missing_database_is_reported_as_insufficient(tmp_path: Path) -> None:
    assessment = assess_corpus(tmp_path / "absent.db")
    assert not assessment.exists
    assert not assessment.sufficient
    assert "no storage database" in assessment.report()


def test_thin_corpus_is_blocked_with_named_reasons(ml_config) -> None:
    windows = _session_windows(
        ml_config,
        user_id="user-a",
        session_id="s1",
        collection_day="2026-01-01",
        apps=[(11, AppCategory.PRODUCTIVITY)],
    )
    assessment = assess_windows(windows, source="unit-test")
    assert not assessment.sufficient
    joined = " ".join(assessment.blocking_reasons)
    assert "enrolled user" in joined
    assert "distinct day" in joined


def test_requirement_summary_states_the_actual_thresholds() -> None:
    summary = data_requirement_summary()
    assert str(MIN_USERS) in summary
    assert str(MIN_DAYS_PER_USER) in summary
    assert str(MIN_SESSIONS_PER_USER) in summary


# ----------------------------------------------------------------------
# Session aggregation
# ----------------------------------------------------------------------
def test_usage_sample_is_a_complete_bounded_vector(ml_config) -> None:
    windows = _session_windows(
        ml_config,
        user_id="user-a",
        session_id="s1",
        collection_day="2026-01-01",
        apps=[(11, AppCategory.PRODUCTIVITY), (22, AppCategory.BROWSING)],
    )
    sample = build_usage_sample("user-a", windows)
    assert set(sample.features) == set(USAGE_FEATURE_NAMES)
    vector = sample.vector()
    assert len(vector) == len(USAGE_FEATURE_NAMES)
    assert all(value == value for value in vector)  # no NaN


def test_category_shares_sum_to_one(ml_config) -> None:
    windows = _session_windows(
        ml_config,
        user_id="user-a",
        session_id="s1",
        collection_day="2026-01-01",
        apps=[(11, AppCategory.PRODUCTIVITY), (22, AppCategory.BROWSING)],
    )
    sample = build_usage_sample("user-a", windows)
    shares = sum(
        value for name, value in sample.features.items() if name.startswith("category_share_")
    )
    assert shares == pytest.approx(1.0)


def test_aggregation_is_deterministic(ml_config) -> None:
    kwargs = dict(
        user_id="user-a",
        session_id="s1",
        collection_day="2026-01-01",
        apps=[(11, AppCategory.PRODUCTIVITY), (22, AppCategory.GAMING)],
    )
    first = build_usage_sample("user-a", _session_windows(ml_config, **kwargs))
    second = build_usage_sample("user-a", _session_windows(ml_config, **kwargs))
    assert first.features == second.features


def test_a_single_application_session_is_maximally_concentrated(ml_config) -> None:
    windows = _session_windows(
        ml_config,
        user_id="user-a",
        session_id="s1",
        collection_day="2026-01-01",
        apps=[(11, AppCategory.PRODUCTIVITY)],
    )
    sample = build_usage_sample("user-a", windows)
    assert sample.features["dominant_app_share"] == pytest.approx(1.0)
    assert sample.features["app_concentration"] == pytest.approx(1.0)
    assert sample.features["category_entropy"] == pytest.approx(0.0)


def test_short_sessions_are_excluded_from_the_sample_set(ml_config) -> None:
    windows = _session_windows(
        ml_config,
        user_id="user-a",
        session_id="s1",
        collection_day="2026-01-01",
        apps=[(11, AppCategory.PRODUCTIVITY)],
        span_us=60_000,
    )
    assert build_usage_samples(windows) == []


# ----------------------------------------------------------------------
# Split discipline
# ----------------------------------------------------------------------
def test_study_refuses_a_split_that_is_not_day_disjoint(ml_config) -> None:
    windows = _session_windows(
        ml_config,
        user_id="user-a",
        session_id="s1",
        collection_day="2026-01-01",
        apps=[(11, AppCategory.PRODUCTIVITY)],
    )
    with pytest.raises(ValueError, match="empty partition"):
        run_study(windows, test_days=["2026-01-01"], plumbing_check=True)
