"""Decide when an A3 scheduled verification prompt is due.

PLAN.md Section 12.2 defines A3 as a deliberate low-frequency prompt whose
purpose is to create verification anchors for otherwise uneventful sessions.

Only a successful A3 resets the clock. A1 anchors a session's first segment
and nothing else, so letting it suppress the next prompt would remove prompts
precisely where A3 is the only mechanism that can anchor a segment, and would
make the effective cadence depend on session length.

This module decides timing only. It never creates an anchor and never
fabricates evidence; a prompt must still be answered correctly by a human.
"""

from __future__ import annotations

from datetime import UTC, datetime


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("scheduled anchor timestamps must include a timezone")
    return value.astimezone(UTC)


class ScheduledAnchorScheduler:
    """Track, per active session, when the next A3 prompt becomes due."""

    def __init__(self, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise ValueError("scheduled anchor interval must be positive")
        self._interval_seconds = float(interval_seconds)
        self._last_anchor: dict[str, datetime] = {}

    def session_started(self, *, session_id: str, at: datetime) -> None:
        self._last_anchor[session_id] = _as_utc(at)

    def anchor_recorded(self, *, session_id: str, at: datetime) -> None:
        if session_id in self._last_anchor:
            self._last_anchor[session_id] = _as_utc(at)

    def due(self, *, session_id: str, now: datetime) -> bool:
        last = self._last_anchor.get(session_id)
        if last is None:
            return False
        return (_as_utc(now) - last).total_seconds() >= self._interval_seconds

    def session_ended(self, *, session_id: str) -> None:
        self._last_anchor.pop(session_id, None)
