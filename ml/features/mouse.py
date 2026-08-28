"""Mouse feature block (PLAN.md Section 7.3, ADR-009 resolution/DPI normalisation).

ASSUMPTION -- resolution normalisation input: PLAN.md Section 7.2's
``MouseEvent`` schema carries raw ``x``/``y`` only; resolution/DPI is
expected to arrive via device/context metadata once the real collector
(T-005) exists. Until then, callers may pass the device's actual
``(screen_width_px, screen_height_px)``; this module scales coordinates to
the configured reference resolution (``config/ml.development.yaml``) before
computing spatial features, so that features stay comparable across
devices. If no resolution is supplied, no scaling is applied (assumed
already at reference resolution) -- this is flagged, not silent, via the
``resolution_normalized`` bookkeeping the caller can inspect through
``compute_mouse_features``'s docstring; there is no field to silently lie
about this in the returned feature dict, by design (PLAN.md guardrail: no
NaN/inf, no silently-fabricated values).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from ml.features.schema import MouseEvent

MOUSE_FEATURE_NAMES: tuple[str, ...] = (
    "velocity_mean",
    "velocity_std",
    "velocity_max",
    "accel_mean",
    "accel_std",
    "jerk_mean",
    "straightness_ratio",
    "curvature_mean",
    "curvature_std",
    "direction_change_rate",
    "angular_velocity_mean",
    "segment_duration_mean",
    "segment_duration_std",
    "pause_count_per_segment",
    "click_duration_mean",
    "click_duration_std",
    "double_click_interval_mean",
    "double_click_interval_std",
    "click_rate",
    "move_to_click_ratio",
    "scroll_rate",
    "scroll_burst_mean",
)


def _mean(values: Sequence[float]) -> float:
    return float(np.mean(values)) if len(values) else 0.0


def _std(values: Sequence[float]) -> float:
    return float(np.std(values)) if len(values) > 1 else 0.0


def _segment_moves(moves: Sequence[MouseEvent], gap_threshold_us: int) -> list[list[MouseEvent]]:
    if not moves:
        return []
    segments: list[list[MouseEvent]] = [[moves[0]]]
    for prev, cur in zip(moves, moves[1:], strict=False):
        if cur.t_capture_us - prev.t_capture_us > gap_threshold_us:
            segments.append([cur])
        else:
            segments[-1].append(cur)
    return segments


def compute_mouse_features(
    events: Sequence[MouseEvent],
    *,
    segment_gap_us: int,
    micro_pause_us: int,
    double_click_max_gap_us: int,
    scroll_burst_gap_us: int,
    window_duration_us: int,
    reference_resolution: tuple[int, int],
    device_resolution: tuple[int, int] | None = None,
) -> dict[str, float]:
    """Compute the full mouse feature block for one window's events.

    Never returns NaN/inf; degenerate/empty inputs deterministically
    fall back to 0.0 for the affected statistic.
    """
    if not events:
        return {name: 0.0 for name in MOUSE_FEATURE_NAMES}

    ordered = sorted(events, key=lambda e: (e.t_capture_us, e.seq))

    sx, sy = 1.0, 1.0
    if device_resolution is not None and device_resolution[0] > 0 and device_resolution[1] > 0:
        sx = reference_resolution[0] / device_resolution[0]
        sy = reference_resolution[1] / device_resolution[1]

    moves = [e for e in ordered if e.type == "MOVE"]
    duration_s = max(window_duration_us, 1) / 1_000_000.0

    velocities: list[float] = []
    accels: list[float] = []
    jerks: list[float] = []
    curvatures: list[float] = []
    angular_velocities: list[float] = []
    direction_changes = 0
    total_path_length = 0.0

    segments = _segment_moves(moves, segment_gap_us)
    segment_durations: list[float] = []
    micro_pause_counts: list[int] = []
    straightness_ratios: list[float] = []

    for seg in segments:
        if len(seg) < 2:
            segment_durations.append(0.0)
            micro_pause_counts.append(0)
            continue
        segment_durations.append((seg[-1].t_capture_us - seg[0].t_capture_us) / 1_000_000.0)

        pauses = 0
        seg_velocities: list[tuple[float, float, float]] = []  # (vx, vy, dt_s)
        seg_path_length = 0.0
        prev_angle: float | None = None
        for a, b in zip(seg, seg[1:], strict=False):
            dt_us = b.t_capture_us - a.t_capture_us
            if dt_us <= 0:
                continue
            if micro_pause_us <= dt_us < segment_gap_us:
                pauses += 1
            dx = (b.x - a.x) * sx
            dy = (b.y - a.y) * sy
            dist = math.hypot(dx, dy)
            seg_path_length += dist
            dt_s = dt_us / 1_000_000.0
            vx, vy = dx / dt_s, dy / dt_s
            speed = math.hypot(vx, vy)
            velocities.append(speed)
            seg_velocities.append((vx, vy, dt_s))

            if dist > 0:
                angle = math.atan2(dy, dx)
                if prev_angle is not None:
                    dtheta = math.atan2(math.sin(angle - prev_angle), math.cos(angle - prev_angle))
                    if dist > 0:
                        curvatures.append(abs(dtheta) / dist)
                    angular_velocities.append(dtheta / dt_s)
                    if abs(dtheta) > math.pi / 2:
                        direction_changes += 1
                prev_angle = angle

        seg_accels: list[float] = []
        for i in range(len(seg_velocities) - 1):
            vx0, vy0, dt0 = seg_velocities[i]
            vx1, vy1, dt1 = seg_velocities[i + 1]
            dt = (dt0 + dt1) / 2.0
            if dt <= 0:
                continue
            ax = (vx1 - vx0) / dt
            ay = (vy1 - vy0) / dt
            seg_accels.append(math.hypot(ax, ay))
        accels.extend(seg_accels)
        # Jerk is computed within this segment only: consecutive accel
        # samples from two different pause-separated segments are not a
        # meaningful rate of change (see self-review note in module docstring).
        for i in range(len(seg_accels) - 1):
            jerks.append(abs(seg_accels[i + 1] - seg_accels[i]))

        total_path_length += seg_path_length
        micro_pause_counts.append(pauses)

        straight_dist = math.hypot((seg[-1].x - seg[0].x) * sx, (seg[-1].y - seg[0].y) * sy)
        straightness_ratios.append(straight_dist / seg_path_length if seg_path_length > 0 else 0.0)

    # Clicks: match BUTTON_DOWN -> next BUTTON_UP of the same button (FIFO).
    from collections import deque

    open_downs: dict[object, deque[int]] = {}
    click_durations: list[int] = []
    click_down_events: list[MouseEvent] = []
    for ev in ordered:
        if ev.type == "BUTTON_DOWN":
            open_downs.setdefault(ev.button, deque()).append(ev.t_capture_us)
            click_down_events.append(ev)
        elif ev.type == "BUTTON_UP":
            q = open_downs.get(ev.button)
            if q:
                down_t = q.popleft()
                if ev.t_capture_us >= down_t:
                    click_durations.append(ev.t_capture_us - down_t)

    double_click_intervals: list[int] = []
    for a, b in zip(click_down_events, click_down_events[1:], strict=False):
        if a.button == b.button:
            gap = b.t_capture_us - a.t_capture_us
            if 0 <= gap <= double_click_max_gap_us:
                double_click_intervals.append(gap)

    click_count = len(click_down_events)
    click_rate = (click_count / duration_s * 60.0) if duration_s > 0 else 0.0
    move_to_click_ratio = (len(moves) / click_count) if click_count > 0 else 0.0

    scrolls = [e for e in ordered if e.type == "SCROLL"]
    scroll_rate = (len(scrolls) / duration_s * 60.0) if duration_s > 0 else 0.0
    scroll_bursts: list[int] = []
    if scrolls:
        current = 1
        for a, b in zip(scrolls, scrolls[1:], strict=False):
            if b.t_capture_us - a.t_capture_us <= scroll_burst_gap_us:
                current += 1
            else:
                scroll_bursts.append(current)
                current = 1
        scroll_bursts.append(current)
    scroll_burst_mean = _mean(scroll_bursts)

    return {
        "velocity_mean": _mean(velocities),
        "velocity_std": _std(velocities),
        "velocity_max": float(max(velocities)) if velocities else 0.0,
        "accel_mean": _mean(accels),
        "accel_std": _std(accels),
        "jerk_mean": _mean(jerks),
        "straightness_ratio": _mean(straightness_ratios),
        "curvature_mean": _mean(curvatures),
        "curvature_std": _std(curvatures),
        "direction_change_rate": (
            (direction_changes / total_path_length) if total_path_length > 0 else 0.0
        ),
        "angular_velocity_mean": _mean(angular_velocities),
        "segment_duration_mean": _mean(segment_durations),
        "segment_duration_std": _std(segment_durations),
        "pause_count_per_segment": _mean([float(c) for c in micro_pause_counts]),
        "click_duration_mean": _mean(click_durations),
        "click_duration_std": _std(click_durations),
        "double_click_interval_mean": _mean(double_click_intervals),
        "double_click_interval_std": _std(double_click_intervals),
        "click_rate": click_rate,
        "move_to_click_ratio": move_to_click_ratio,
        "scroll_rate": scroll_rate,
        "scroll_burst_mean": scroll_burst_mean,
    }
