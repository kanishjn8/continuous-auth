"""Bounded sequence continuity, duplicate, and late-event detection."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal

OrderingStatus = Literal["ACCEPTED", "GAP_ACCEPTED", "DUPLICATE", "OUT_OF_ORDER"]


@dataclass(frozen=True)
class OrderingResult:
    status: OrderingStatus
    missing_sequences: int = 0

    @property
    def accepted(self) -> bool:
        return self.status in ("ACCEPTED", "GAP_ACCEPTED")


class SequenceTracker:
    """No-wrap sequence policy: a reset to zero is late, not a valid wrap."""

    def __init__(self, history_capacity: int):
        if history_capacity <= 0:
            raise ValueError("history_capacity must be positive")
        self._history_capacity = history_capacity
        self._recent = deque[int]()
        self._recent_set: set[int] = set()
        self._expected: int | None = None
        self._last_capture_us: int | None = None

    @property
    def expected_sequence(self) -> int | None:
        return self._expected

    def _remember(self, sequence: int) -> None:
        if len(self._recent) == self._history_capacity:
            removed = self._recent.popleft()
            self._recent_set.remove(removed)
        self._recent.append(sequence)
        self._recent_set.add(sequence)

    def observe(self, sequence: int, t_capture_us: int | None = None) -> OrderingResult:
        if self._expected is None:
            self._expected = sequence + 1
            self._remember(sequence)
            self._last_capture_us = t_capture_us
            return OrderingResult("ACCEPTED")
        if sequence < self._expected:
            if sequence in self._recent_set:
                return OrderingResult("DUPLICATE")
            return OrderingResult("OUT_OF_ORDER")

        missing = sequence - self._expected
        self._expected = sequence + 1
        self._remember(sequence)
        if (
            t_capture_us is not None
            and self._last_capture_us is not None
            and t_capture_us < self._last_capture_us
        ):
            return OrderingResult("OUT_OF_ORDER", missing_sequences=missing)
        self._last_capture_us = t_capture_us
        if missing:
            return OrderingResult("GAP_ACCEPTED", missing_sequences=missing)
        return OrderingResult("ACCEPTED")

    def reset(self) -> None:
        self._recent.clear()
        self._recent_set.clear()
        self._expected = None
        self._last_capture_us = None
