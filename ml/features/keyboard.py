"""Keyboard feature block (PLAN.md Section 7.3).

All features are computed from ``KeyClass`` + timing only (ADR-004) — no
function here ever sees a keycode or character, and no field of
``KeyboardEvent`` could carry one (see ``ml/features/schema.py``).

ASSUMPTION -- digraph surrogate via class transitions (ADR-004 rationale):
"cross-hand", "same-hand" and "same-row" transition buckets are only
well-defined for the alphabetic key classes (``KEY_CLASS_HAND`` /
``KEY_CLASS_ROW`` in ``schema.py``). A transition where either class lacks a
defined hand/row (e.g. involves DIGIT, PUNCT, SPACE, ...) is not counted in
any of the three buckets. This is a deliberate simplification, not specified
by PLAN.md, and should be revisited once real typing data is available.

ASSUMPTION -- key-down/key-up pairing: the IPC schema (PLAN.md Section 7.2)
identifies keys only by class, not by physical key identity, so several
physically-distinct keys of the same class pressed concurrently cannot be
told apart. Dwell/rollover pairing therefore matches KEY_UP to the oldest
still-open KEY_DOWN of the *same class* (FIFO per class). This is a
reasonable approximation given the content-free schema and is exact for the
overwhelmingly common case of one key of a given class held at a time.
"""

from __future__ import annotations

from collections import deque
from typing import Sequence

import numpy as np

from ml.features.schema import (
    CORRECTION_CLASSES,
    HOME_ROW_CLASSES,
    KEY_CLASS_HAND,
    KEY_CLASS_ROW,
    KeyClass,
    KeyboardEvent,
)

# The full, ordered set of keyboard feature names this module produces.
# Kept as a module-level constant so tests and downstream code (e.g. model
# input assembly) can assert completeness against PLAN.md Section 7.3.
KEYBOARD_FEATURE_NAMES: tuple[str, ...] = (
    "dwell_mean",
    "dwell_std",
    "dwell_median",
    "dwell_p90",
    "flight_mean",
    "flight_std",
    "dd_latency_mean",
    "dd_latency_std",
    "typing_rate",
    "burst_rate",
    "burst_mean_length",
    "pause_ratio",
    "iki_cv",
    "backspace_ratio",
    "correction_burst_rate",
    "modifier_ratio",
    "rollover_ratio",
    "xhand_latency_mean",
    "xhand_latency_std",
    "samehand_latency_mean",
    "samehand_latency_std",
    "samerow_latency_mean",
    "samerow_latency_std",
    "homerow_dwell_ratio",
)


def _mean(values: Sequence[float]) -> float:
    return float(np.mean(values)) if values else 0.0


def _std(values: Sequence[float]) -> float:
    return float(np.std(values)) if len(values) > 1 else 0.0


def _median(values: Sequence[float]) -> float:
    return float(np.median(values)) if values else 0.0


def _p90(values: Sequence[float]) -> float:
    return float(np.percentile(values, 90)) if values else 0.0


def _match_dwell_pairs(events: Sequence[KeyboardEvent]) -> list[tuple[KeyClass, int, int]]:
    """Return list of (key_class, down_t_us, up_t_us) for matched, non-repeat presses."""
    open_downs: dict[KeyClass, deque[int]] = {}
    pairs: list[tuple[KeyClass, int, int]] = []
    for ev in events:
        if ev.type == "KEY_DOWN":
            if ev.is_repeat:
                continue
            open_downs.setdefault(ev.key_class, deque()).append(ev.t_capture_us)
        elif ev.type == "KEY_UP":
            q = open_downs.get(ev.key_class)
            if q:
                down_t = q.popleft()
                pairs.append((ev.key_class, down_t, ev.t_capture_us))
    return pairs


def compute_keyboard_features(
    events: Sequence[KeyboardEvent],
    *,
    pause_threshold_us: int,
    burst_gap_threshold_us: int,
    window_duration_us: int,
) -> dict[str, float]:
    """Compute the full keyboard feature block for one window's events.

    Callers (windowing.py) are responsible for the ADR-005 quality gate --
    this function always computes on whatever non-empty event list it is
    given, and never returns NaN/inf (deterministic zero-fallbacks are used
    for undefined statistics on tiny/degenerate inputs, per the "no NaN
    leakage into stored vectors" acceptance criterion for T-008).
    """
    if not events:
        return {name: 0.0 for name in KEYBOARD_FEATURE_NAMES}

    ordered = sorted(events, key=lambda e: (e.t_capture_us, e.seq))
    key_downs = [e for e in ordered if e.type == "KEY_DOWN" and not e.is_repeat]

    pairs = _match_dwell_pairs(ordered)
    dwells = [up - down for _, down, up in pairs if up >= down]

    # Down-down latency (consecutive non-repeat key-downs).
    dd_latencies = [
        key_downs[i + 1].t_capture_us - key_downs[i].t_capture_us for i in range(len(key_downs) - 1)
    ]
    dd_latencies = [d for d in dd_latencies if d >= 0]

    # Flight time: KEY_UP -> next KEY_DOWN, computed on the full ordered
    # stream (not per-class) since flight time is a motor-timing feature
    # between successive keystrokes regardless of class.
    flights: list[int] = []
    last_up_t: int | None = None
    for ev in ordered:
        if ev.type == "KEY_UP":
            last_up_t = ev.t_capture_us
        elif ev.type == "KEY_DOWN" and not ev.is_repeat and last_up_t is not None:
            gap = ev.t_capture_us - last_up_t
            if gap >= 0:
                flights.append(gap)
            last_up_t = None

    total_keys = len(key_downs)
    duration_s = max(window_duration_us, 1) / 1_000_000.0

    typing_rate = total_keys / duration_s if duration_s > 0 else 0.0

    # Burst detection over dd_latencies.
    burst_lengths: list[int] = []
    current = 1
    for gap in dd_latencies:
        if gap <= burst_gap_threshold_us:
            current += 1
        else:
            if current > 1:
                burst_lengths.append(current)
            current = 1
    if current > 1:
        burst_lengths.append(current)
    burst_rate = (len(burst_lengths) / duration_s * 60.0) if duration_s > 0 else 0.0
    burst_mean_length = _mean(burst_lengths) if burst_lengths else 0.0

    pause_ratio = (
        sum(1 for g in dd_latencies if g > pause_threshold_us) / len(dd_latencies)
        if dd_latencies
        else 0.0
    )

    dd_mean = _mean(dd_latencies)
    iki_cv = (_std(dd_latencies) / dd_mean) if dd_mean > 0 else 0.0

    correction_count = sum(1 for e in key_downs if e.key_class in CORRECTION_CLASSES)
    backspace_ratio = correction_count / total_keys if total_keys else 0.0

    correction_runs = 0
    run_len = 0
    for e in key_downs:
        if e.key_class in CORRECTION_CLASSES:
            run_len += 1
        else:
            if run_len >= 2:
                correction_runs += 1
            run_len = 0
    if run_len >= 2:
        correction_runs += 1
    correction_burst_rate = (correction_runs / duration_s * 60.0) if duration_s > 0 else 0.0

    modifier_count = sum(1 for e in key_downs if e.key_class == KeyClass.MODIFIER)
    modifier_ratio = modifier_count / total_keys if total_keys else 0.0

    # Rollover: next key pressed before the current key's release.
    rollover_count = 0
    if len(pairs) >= 2:
        pairs_by_down = sorted(pairs, key=lambda p: p[1])
        for i in range(len(pairs_by_down) - 1):
            _, _, up_i = pairs_by_down[i]
            _, down_next, _ = pairs_by_down[i + 1]
            if down_next < up_i:
                rollover_count += 1
        rollover_ratio = rollover_count / (len(pairs_by_down) - 1)
    else:
        rollover_ratio = 0.0

    # Class-transition latency buckets (digraph surrogate, ADR-004).
    xhand: list[int] = []
    samehand: list[int] = []
    samerow: list[int] = []
    for i in range(len(key_downs) - 1):
        a, b = key_downs[i], key_downs[i + 1]
        hand_a, hand_b = KEY_CLASS_HAND.get(a.key_class), KEY_CLASS_HAND.get(b.key_class)
        if hand_a is None or hand_b is None:
            continue
        latency = b.t_capture_us - a.t_capture_us
        if latency < 0:
            continue
        if hand_a != hand_b:
            xhand.append(latency)
        else:
            row_a, row_b = KEY_CLASS_ROW.get(a.key_class), KEY_CLASS_ROW.get(b.key_class)
            if row_a == row_b:
                samerow.append(latency)
            else:
                samehand.append(latency)

    home_dwell = sum(up - down for cls, down, up in pairs if cls in HOME_ROW_CLASSES and up >= down)
    total_dwell = sum(dwells) if dwells else 0
    homerow_dwell_ratio = (home_dwell / total_dwell) if total_dwell > 0 else 0.0

    return {
        "dwell_mean": _mean(dwells),
        "dwell_std": _std(dwells),
        "dwell_median": _median(dwells),
        "dwell_p90": _p90(dwells),
        "flight_mean": _mean(flights),
        "flight_std": _std(flights),
        "dd_latency_mean": dd_mean,
        "dd_latency_std": _std(dd_latencies),
        "typing_rate": typing_rate,
        "burst_rate": burst_rate,
        "burst_mean_length": burst_mean_length,
        "pause_ratio": pause_ratio,
        "iki_cv": iki_cv,
        "backspace_ratio": backspace_ratio,
        "correction_burst_rate": correction_burst_rate,
        "modifier_ratio": modifier_ratio,
        "rollover_ratio": rollover_ratio,
        "xhand_latency_mean": _mean(xhand),
        "xhand_latency_std": _std(xhand),
        "samehand_latency_mean": _mean(samehand),
        "samehand_latency_std": _std(samehand),
        "samerow_latency_mean": _mean(samerow),
        "samerow_latency_std": _std(samerow),
        "homerow_dwell_ratio": homerow_dwell_ratio,
    }
