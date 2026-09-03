"""A3 prompt scheduling: only a successful A3 resets the clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.app.updates.anchors import ScheduledAnchorScheduler

FOUR_HOURS = 4 * 3600.0
START = datetime(2026, 9, 3, 9, 0, tzinfo=UTC)


def _scheduler() -> ScheduledAnchorScheduler:
    scheduler = ScheduledAnchorScheduler(FOUR_HOURS)
    scheduler.session_started(session_id="s1", at=START)
    return scheduler


def test_not_due_before_the_interval_elapses() -> None:
    scheduler = _scheduler()
    assert scheduler.due(session_id="s1", now=START + timedelta(hours=3, minutes=59)) is False


def test_due_once_the_interval_elapses() -> None:
    scheduler = _scheduler()
    assert scheduler.due(session_id="s1", now=START + timedelta(hours=4)) is True


def test_recording_an_anchor_resets_the_clock() -> None:
    scheduler = _scheduler()
    at = START + timedelta(hours=4)
    assert scheduler.due(session_id="s1", now=at) is True
    scheduler.anchor_recorded(session_id="s1", at=at)
    assert scheduler.due(session_id="s1", now=at + timedelta(hours=3)) is False
    assert scheduler.due(session_id="s1", now=at + timedelta(hours=4)) is True


def test_unknown_session_is_never_due() -> None:
    scheduler = ScheduledAnchorScheduler(FOUR_HOURS)
    assert scheduler.due(session_id="absent", now=START) is False


def test_ended_session_is_never_due() -> None:
    scheduler = _scheduler()
    scheduler.session_ended(session_id="s1")
    assert scheduler.due(session_id="s1", now=START + timedelta(hours=9)) is False


def test_naive_timestamps_are_rejected() -> None:
    scheduler = ScheduledAnchorScheduler(FOUR_HOURS)
    with pytest.raises(ValueError):
        scheduler.session_started(session_id="s1", at=datetime(2026, 9, 3, 9, 0))


def test_non_positive_interval_is_rejected() -> None:
    with pytest.raises(ValueError):
        ScheduledAnchorScheduler(0.0)
