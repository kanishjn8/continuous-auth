"""Authenticated session and idle-segment lifecycle state."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from .errors import IngestionLifecycleError
from .types import (
    AttributedEvent,
    AuthenticatedEntry,
    IngestedEvent,
    InputEvent,
    LifecycleEvent,
)

IdFactory = Callable[[str], str]


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


@dataclass
class _ActiveSession:
    user_id: str
    session_id: str
    entry_auth_evidence: str
    wall_clock_anchor: datetime
    segment_id: str | None = None
    last_input_us: int | None = None
    last_event_us: int | None = None


class SessionManager:
    def __init__(self, *, idle_split_us: int, id_factory: IdFactory | None = None):
        if idle_split_us <= 0:
            raise ValueError("idle_split_us must be positive")
        self._idle_split_us = idle_split_us
        self._id_factory = id_factory or _new_id
        self._active: _ActiveSession | None = None

    @property
    def active_session_id(self) -> str | None:
        return self._active.session_id if self._active is not None else None

    def start(self, entry: AuthenticatedEntry) -> LifecycleEvent:
        if self._active is not None:
            raise IngestionLifecycleError("cannot start a session while another is active")
        if not entry.user_id.strip() or not entry.evidence_id.strip():
            raise IngestionLifecycleError("authenticated entry identifiers must not be blank")
        if entry.wall_clock_anchor.tzinfo is None or entry.wall_clock_anchor.utcoffset() is None:
            raise IngestionLifecycleError("wall_clock_anchor must be timezone-aware")
        if not entry.authenticated:
            return LifecycleEvent(
                kind="SESSION_START_REJECTED",
                user_id=entry.user_id,
                session_id=None,
                segment_id=None,
                t_capture_us=None,
                reason="ENTRY_AUTHENTICATION_FAILED",
                wall_clock_anchor=entry.wall_clock_anchor,
                entry_auth_evidence=entry.evidence_id,
            )
        session_id = entry.session_id or self._id_factory("session")
        if not session_id.strip():
            raise IngestionLifecycleError("session_id must not be blank")
        self._active = _ActiveSession(
            user_id=entry.user_id,
            session_id=session_id,
            entry_auth_evidence=entry.evidence_id,
            wall_clock_anchor=entry.wall_clock_anchor,
        )
        return LifecycleEvent(
            kind="SESSION_STARTED",
            user_id=entry.user_id,
            session_id=session_id,
            segment_id=None,
            t_capture_us=None,
            reason="AUTHENTICATED_ENTRY",
            wall_clock_anchor=entry.wall_clock_anchor,
            entry_auth_evidence=entry.evidence_id,
        )

    def attribute(
        self, event: IngestedEvent
    ) -> tuple[AttributedEvent | None, list[LifecycleEvent]]:
        session = self._active
        if session is None:
            return None, []
        lifecycle: list[LifecycleEvent] = []
        is_input = isinstance(event, InputEvent)
        if (
            is_input
            and session.last_input_us is not None
            and event.t_capture_us - session.last_input_us > self._idle_split_us
        ):
            if session.segment_id is not None:
                lifecycle.append(
                    LifecycleEvent(
                        kind="SEGMENT_ENDED",
                        user_id=session.user_id,
                        session_id=session.session_id,
                        segment_id=session.segment_id,
                        t_capture_us=event.t_capture_us,
                        reason="IDLE_SPLIT",
                    )
                )
            session.segment_id = None

        if session.segment_id is None:
            session.segment_id = self._id_factory("segment")
            lifecycle.append(
                LifecycleEvent(
                    kind="SEGMENT_STARTED",
                    user_id=session.user_id,
                    session_id=session.session_id,
                    segment_id=session.segment_id,
                    t_capture_us=event.t_capture_us,
                    reason="FIRST_EVENT" if session.last_event_us is None else "INPUT_RESUMED",
                )
            )

        session.last_event_us = event.t_capture_us
        if is_input:
            session.last_input_us = event.t_capture_us
        return (
            AttributedEvent(
                event=event,
                user_id=session.user_id,
                session_id=session.session_id,
                segment_id=session.segment_id,
            ),
            lifecycle,
        )

    def end(self, reason: str) -> list[LifecycleEvent]:
        session = self._active
        if session is None:
            raise IngestionLifecycleError("cannot end a session when none is active")
        lifecycle: list[LifecycleEvent] = []
        if session.segment_id is not None:
            lifecycle.append(
                LifecycleEvent(
                    kind="SEGMENT_ENDED",
                    user_id=session.user_id,
                    session_id=session.session_id,
                    segment_id=session.segment_id,
                    t_capture_us=session.last_event_us,
                    reason=reason,
                )
            )
        lifecycle.append(
            LifecycleEvent(
                kind="SESSION_ENDED",
                user_id=session.user_id,
                session_id=session.session_id,
                segment_id=None,
                t_capture_us=session.last_event_us,
                reason=reason,
            )
        )
        self._active = None
        return lifecycle
