"""Incremental four-byte network-order C1 frame decoder."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Literal

FRAME_HEADER = struct.Struct(">I")

FrameIssueCode = Literal["ZERO_LENGTH_FRAME", "OVERSIZED_FRAME", "TRUNCATED_FRAME"]


@dataclass(frozen=True)
class FrameIssue:
    code: FrameIssueCode
    detail: str


@dataclass(frozen=True)
class DecodeResult:
    payloads: tuple[bytes, ...]
    issues: tuple[FrameIssue, ...]


class IncrementalFrameDecoder:
    """Decode arbitrary chunks while keeping memory bounded by one frame."""

    def __init__(self, max_frame_bytes: int):
        if max_frame_bytes <= 0:
            raise ValueError("max_frame_bytes must be positive")
        self._max_frame_bytes = max_frame_bytes
        self._buffer = bytearray()
        self._expected_payload_bytes: int | None = None

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, chunk: bytes | bytearray | memoryview) -> DecodeResult:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("frame chunk must be bytes-like")
        self._buffer.extend(chunk)
        payloads: list[bytes] = []
        issues: list[FrameIssue] = []

        while True:
            if self._expected_payload_bytes is None:
                if len(self._buffer) < FRAME_HEADER.size:
                    break
                (declared_size,) = FRAME_HEADER.unpack(self._buffer[: FRAME_HEADER.size])
                del self._buffer[: FRAME_HEADER.size]
                if declared_size == 0:
                    issues.append(FrameIssue("ZERO_LENGTH_FRAME", "frame declared zero bytes"))
                    continue
                if declared_size > self._max_frame_bytes:
                    issues.append(
                        FrameIssue(
                            "OVERSIZED_FRAME",
                            f"declared {declared_size} bytes exceeds {self._max_frame_bytes}",
                        )
                    )
                    # Cannot safely resynchronize within a length-prefixed stream once a
                    # frame exceeds the configured bound; discard buffered state to avoid
                    # cascading misparses of the oversized payload as headers.
                    self.reset()
                    break
                        )
                    )
                    continue
                self._expected_payload_bytes = declared_size

            expected = self._expected_payload_bytes
            if expected is None or len(self._buffer) < expected:
                break
            payloads.append(bytes(self._buffer[:expected]))
            del self._buffer[:expected]
            self._expected_payload_bytes = None

        return DecodeResult(tuple(payloads), tuple(issues))

    def finish(self) -> DecodeResult:
        if self._expected_payload_bytes is None and not self._buffer:
            return DecodeResult((), ())
        expected = self._expected_payload_bytes
        detail = (
            f"stream ended with {len(self._buffer)} of {expected} payload bytes"
            if expected is not None
            else f"stream ended with {len(self._buffer)} header bytes"
        )
        self.reset()
        return DecodeResult((), (FrameIssue("TRUNCATED_FRAME", detail),))

    def reset(self) -> None:
        self._buffer.clear()
        self._expected_payload_bytes = None
