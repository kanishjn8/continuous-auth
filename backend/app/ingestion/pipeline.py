"""T-007 orchestration: frames to bounded, attributed C1 events."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from .config import IngestionSettings
from .framing import DecodeResult, FrameIssue, IncrementalFrameDecoder
from .ordering import SequenceTracker
from .sessions import IdFactory, SessionManager
from .types import (
    AttributedEvent,
    AuthenticatedEntry,
    AvailabilityEvent,
    IngestionBatch,
    IngestionCounters,
    LifecycleEvent,
)
from .validation import ValidationIssue, validate_payload

EventSink = Callable[[AttributedEvent], None]
LifecycleSink = Callable[[LifecycleEvent], None]
AvailabilitySink = Callable[[AvailabilityEvent], None]


class IngestionPipeline:
    """Stateful ingestion service; raw event retention is bounded and memory-only."""

    def __init__(
        self,
        settings: IngestionSettings,
        *,
        event_sink: EventSink | None = None,
        lifecycle_sink: LifecycleSink | None = None,
        availability_sink: AvailabilitySink | None = None,
        id_factory: IdFactory | None = None,
    ):
        self.settings = settings
        self._decoder = IncrementalFrameDecoder(settings.max_frame_bytes)
        self._ordering = SequenceTracker(settings.raw_event_ring_capacity)
        self._sessions = SessionManager(
            idle_split_us=settings.idle_split_us,
            id_factory=id_factory,
        )
        self._raw_ring = deque[AttributedEvent](maxlen=settings.raw_event_ring_capacity)
        self._event_sink = event_sink
        self._lifecycle_sink = lifecycle_sink
        self._availability_sink = availability_sink
        self._counters = IngestionCounters()

    @property
    def counters(self) -> IngestionCounters:
        return self._counters.snapshot()

    @property
    def raw_event_ring(self) -> tuple[AttributedEvent, ...]:
        return tuple(self._raw_ring)

    @property
    def active_session_id(self) -> str | None:
        return self._sessions.active_session_id

    def _emit_lifecycle(self, events: list[LifecycleEvent]) -> None:
        if self._lifecycle_sink is not None:
            for event in events:
                self._lifecycle_sink(event)

    def _availability(self, code: str, component: str, detail: str) -> AvailabilityEvent:
        event = AvailabilityEvent(code=code, component=component, detail=detail)
        self._counters.availability_events += 1
        if self._availability_sink is not None:
            self._availability_sink(event)
        return event

    def start_session(self, entry: AuthenticatedEntry) -> LifecycleEvent:
        event = self._sessions.start(entry)
        if event.kind == "SESSION_STARTED":
            self._ordering.reset()
            self._raw_ring.clear()
        self._emit_lifecycle([event])
        return event

    def end_session(self, reason: str = "AUTHENTICATED_EXIT") -> tuple[LifecycleEvent, ...]:
        events = self._sessions.end(reason)
        self._emit_lifecycle(events)
        self._ordering.reset()
        self._raw_ring.clear()
        return tuple(events)

    def _record_frame_issue(self, issue: FrameIssue) -> AvailabilityEvent:
        if issue.code == "OVERSIZED_FRAME":
            self._counters.oversized_frames += 1
        elif issue.code == "ZERO_LENGTH_FRAME":
            self._counters.zero_length_frames += 1
        else:
            self._counters.truncated_frames += 1
        return self._availability(issue.code, "transport", issue.detail)

    def _record_validation_issue(self, issue: ValidationIssue) -> AvailabilityEvent:
        if issue.code == "MALFORMED_JSON":
            self._counters.malformed_frames += 1
        else:
            self._counters.schema_rejections += 1
        return self._availability(issue.code, "protocol", issue.detail)

    def _consume_decoded(self, decoded: DecodeResult) -> IngestionBatch:
        accepted: list[AttributedEvent] = []
        lifecycle: list[LifecycleEvent] = []
        availability = [self._record_frame_issue(issue) for issue in decoded.issues]
        for payload in decoded.payloads:
            self._counters.frames_received += 1
            validated = validate_payload(payload)
            if validated.issue is not None:
                availability.append(self._record_validation_issue(validated.issue))
                continue
            event = validated.event
            assert event is not None
            ordering = self._ordering.observe(event.seq, event.t_capture_us)
            if ordering.missing_sequences:
                self._counters.missing_sequences += ordering.missing_sequences
            if ordering.status == "DUPLICATE":
                self._counters.duplicate_rejections += 1
                continue
            if ordering.status == "OUT_OF_ORDER":
                self._counters.out_of_order_rejections += 1
                continue
            attributed, changes = self._sessions.attribute(event)
            if attributed is None:
                self._counters.inactive_session_rejections += 1
                availability.append(
                    self._availability(
                        "INACTIVE_SESSION",
                        "session",
                        "validated event rejected because no authenticated session is active",
                    )
                )
                continue
            lifecycle.extend(changes)
            self._emit_lifecycle(changes)
            if len(self._raw_ring) == self._raw_ring.maxlen:
                self._counters.raw_ring_overwrites += 1
            self._raw_ring.append(attributed)
            self._counters.events_accepted += 1
            accepted.append(attributed)
            if self._event_sink is not None:
                self._event_sink(attributed)

        return IngestionBatch(
            events=tuple(accepted),
            lifecycle=tuple(lifecycle),
            availability=tuple(availability),
            counters=self.counters,
        )

    def feed(self, chunk: bytes | bytearray | memoryview) -> IngestionBatch:
        self._counters.chunks_received += 1
        return self._consume_decoded(self._decoder.feed(chunk))

    def finish_transport(self) -> IngestionBatch:
        return self._consume_decoded(self._decoder.finish())

    def report_infrastructure_failure(
        self, *, code: str, component: str, detail: str
    ) -> AvailabilityEvent:
        if not code.strip() or not component.strip() or not detail.strip():
            raise ValueError("availability failure fields must not be blank")
        return self._availability(code, component, detail)

    def recover_transport(self) -> IngestionBatch:
        """Discard only an incomplete transport frame; session/sequence state survives."""

        batch = self.finish_transport()
        self._decoder.reset()
        return batch

    def shutdown(self) -> tuple[LifecycleEvent, ...]:
        self.finish_transport()
        if self.active_session_id is None:
            return ()
        return self.end_session("SHUTDOWN")
