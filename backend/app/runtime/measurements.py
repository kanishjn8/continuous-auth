"""Bounded processing-latency measurements for health and benchmark evidence."""

from __future__ import annotations

from collections import deque


class RuntimeMeasurements:
    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("measurement capacity must be positive")
        self._values: dict[str, deque[float]] = {
            "frame_batch": deque(maxlen=capacity),
            "feature_score_decision": deque(maxlen=capacity),
        }

    def record(self, stage: str, latency_us: float) -> None:
        if stage not in self._values:
            raise ValueError("unknown measurement stage")
        if latency_us < 0:
            raise ValueError("latency cannot be negative")
        self._values[stage].append(latency_us)

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        position = (len(ordered) - 1) * percentile
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = position - lower
        return ordered[lower] * (1 - fraction) + ordered[upper] * fraction

    def snapshot(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for stage, values in self._values.items():
            copied = list(values)
            result[f"{stage}_p50"] = self._percentile(copied, 0.50)
            result[f"{stage}_p95"] = self._percentile(copied, 0.95)
            result[f"{stage}_p99"] = self._percentile(copied, 0.99)
        return result
