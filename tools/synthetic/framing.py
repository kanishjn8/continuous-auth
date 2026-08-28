"""Canonical T-003/T-007 fixture framing and fault injection.

Frames use a four-byte unsigned network-order payload length followed by
canonical UTF-8 JSON. The real T-005 transport remains swappable, but its
producer/consumer fixture contract can use these bytes on every platform.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass

from pydantic import TypeAdapter

from ml.features.schema import ContextEvent, Heartbeat, KeyboardEvent, MouseEvent
from protocol.generated.python.contracts import EventFrame
from tools.synthetic.config import FaultConfig

SyntheticEvent = KeyboardEvent | MouseEvent | ContextEvent | Heartbeat
EVENT_ADAPTER: TypeAdapter[EventFrame] = TypeAdapter(EventFrame)
FRAME_HEADER = struct.Struct(">I")


class SyntheticFrameError(ValueError):
    """Raised before any invalid synthetic stream is returned."""


@dataclass(frozen=True)
class FramedStreams:
    normal: bytes
    gap: bytes
    duplicate: bytes
    out_of_order: bytes
    malformed: bytes
    oversized: bytes


def canonical_payload(event: SyntheticEvent) -> bytes:
    """Validate against C1, then serialize deterministically."""

    document = event.model_dump(mode="json")
    EVENT_ADAPTER.validate_python(document)
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def frame_payload(payload: bytes, *, max_frame_bytes: int) -> bytes:
    if not payload:
        raise SyntheticFrameError("frame payload must not be empty")
    if len(payload) > max_frame_bytes:
        raise SyntheticFrameError(
            f"payload size {len(payload)} exceeds configured maximum {max_frame_bytes}"
        )
    return FRAME_HEADER.pack(len(payload)) + payload


def frame_event(event: SyntheticEvent, *, max_frame_bytes: int) -> bytes:
    return frame_payload(canonical_payload(event), max_frame_bytes=max_frame_bytes)


def build_framed_streams(
    events: tuple[SyntheticEvent, ...],
    *,
    faults: FaultConfig,
    max_frame_bytes: int,
) -> FramedStreams:
    """Return a valid stream plus isolated gap/duplicate/order/frame faults."""

    frames = [frame_event(event, max_frame_bytes=max_frame_bytes) for event in events]
    if not frames:
        raise SyntheticFrameError("cannot frame an empty scenario")

    indexed_faults = {
        "gap_event_index": faults.gap_event_index,
        "duplicate_event_index": faults.duplicate_event_index,
        "out_of_order_event_index": faults.out_of_order_event_index,
    }
    for field, index in indexed_faults.items():
        if index >= len(frames):
            raise SyntheticFrameError(f"{field}={index} is outside {len(frames)} generated events")
    if faults.out_of_order_event_index + 1 >= len(frames):
        raise SyntheticFrameError("out_of_order_event_index must have a following event to swap")

    gap_frames = frames.copy()
    del gap_frames[faults.gap_event_index]

    duplicate_frames = frames.copy()
    duplicate_frames.insert(
        faults.duplicate_event_index + 1,
        frames[faults.duplicate_event_index],
    )

    order_frames = frames.copy()
    left = faults.out_of_order_event_index
    order_frames[left], order_frames[left + 1] = order_frames[left + 1], order_frames[left]

    malformed_payload = faults.malformed_payload.encode("utf-8")
    malformed = b"".join(frames) + frame_payload(
        malformed_payload,
        max_frame_bytes=max_frame_bytes,
    )
    oversized = FRAME_HEADER.pack(max_frame_bytes + 1)

    return FramedStreams(
        normal=b"".join(frames),
        gap=b"".join(gap_frames),
        duplicate=b"".join(duplicate_frames),
        out_of_order=b"".join(order_frames),
        malformed=malformed,
        oversized=oversized,
    )
