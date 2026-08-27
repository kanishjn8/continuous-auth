"""In-memory-only ingestion outputs and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias

from ml.features.schema import ContextEvent, Heartbeat, KeyboardEvent, MouseEvent
from protocol.generated.python.contracts import AppRegistryEvent, DeviceMetadataEvent

IngestedEvent: TypeAlias = (
    KeyboardEvent | MouseEvent | ContextEvent | Heartbeat | AppRegistryEvent | DeviceMetadataEvent
)
InputEvent: TypeAlias = KeyboardEvent | MouseEvent


@dataclass(frozen=True)
class AuthenticatedEntry:
    """Evidence supplied by the local login/unlock authentication boundary."""

    user_id: str
    evidence_id: str
    authenticated: bool
    wall_clock_anchor: datetime
    session_id: str | None = None


@dataclass(frozen=True)
class AttributedEvent:
    """A validated C1 event with in-memory session and segment attribution."""

    event: IngestedEvent
    user_id: str
    session_id: str
    segment_id: str


LifecycleKind = Literal[
    "SESSION_STARTED",
    "SESSION_ENDED",
    "SESSION_START_REJECTED",
    "SEGMENT_STARTED",
    "SEGMENT_ENDED",
]


@dataclass(frozen=True)
class LifecycleEvent:
    kind: LifecycleKind
    user_id: str
    session_id: str | None
    segment_id: str | None
    t_capture_us: int | None
    reason: str
    wall_clock_anchor: datetime | None = None
    entry_auth_evidence: str | None = None


@dataclass(frozen=True)
class AvailabilityEvent:
    code: str
    component: str
    detail: str


@dataclass
class IngestionCounters:
    chunks_received: int = 0
    frames_received: int = 0
    events_accepted: int = 0
    malformed_frames: int = 0
    schema_rejections: int = 0
    oversized_frames: int = 0
    zero_length_frames: int = 0
    truncated_frames: int = 0
    missing_sequences: int = 0
    duplicate_rejections: int = 0
    out_of_order_rejections: int = 0
    inactive_session_rejections: int = 0
    raw_ring_overwrites: int = 0
    availability_events: int = 0

    def snapshot(self) -> IngestionCounters:
        return IngestionCounters(**self.__dict__)


@dataclass(frozen=True)
class IngestionBatch:
    events: tuple[AttributedEvent, ...]
    lifecycle: tuple[LifecycleEvent, ...]
    availability: tuple[AvailabilityEvent, ...]
    counters: IngestionCounters
