"""Deterministic synthetic keyboard/mouse/context event generator.

PLAN.md Section 9.5: "A synthetic event-stream generator with configurable,
deterministic timing parameters is required early... This is essential for
automated development: an agent cannot be blocked waiting for human pilot
data, and must never be given real participant data as a development
fixture." PLAN.md Section 9.6: synthetic data "may train models" for
mechanics validation only, and "may [not] produce headline results."

The full T-003 generator lives in ``tools.synthetic`` and adds authoritative
C1 framing, transport faults, C2 outputs, and score/risk assertions. This
module remains a lighter-weight ML corpus helper for T-008/T-010/T-011 tests.

Every event produced here carries ``Provenance.SYNTHETIC``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from ml.features.schema import (
    AppCategory,
    ContextEvent,
    DeviceClass,
    KeyboardEvent,
    KeyClass,
    MouseButton,
    MouseEvent,
)

# QWERTY-adjacent alphabetic classes used for weighted random key selection.
_ALPHA_CLASSES = (
    KeyClass.ALPHA_L_HOME,
    KeyClass.ALPHA_L_UPPER,
    KeyClass.ALPHA_L_LOWER,
    KeyClass.ALPHA_R_HOME,
    KeyClass.ALPHA_R_UPPER,
    KeyClass.ALPHA_R_LOWER,
)


@dataclass(frozen=True)
class SyntheticUserProfile:
    """Deterministic per-"user" behavioral parameters.

    Values are loosely realistic (order-of-magnitude plausible dwell/flight
    times in microseconds) but are not fit to any real data -- they exist to
    give distinct, separable synthetic identities for pipeline mechanics
    testing, per PLAN.md Section 9.6.
    """

    user_id: str
    mean_dwell_us: float = 90_000.0
    std_dwell_us: float = 15_000.0
    mean_dd_latency_us: float = 200_000.0
    std_dd_latency_us: float = 45_000.0
    backspace_rate: float = 0.05
    modifier_rate: float = 0.03
    space_rate: float = 0.15
    mean_mouse_speed_px_s: float = 800.0
    std_mouse_speed_px_s: float = 200.0
    mouse_step_dt_us: float = 40_000.0
    click_rate_per_min: float = 15.0
    scroll_rate_per_min: float = 10.0
    preferred_app_category: AppCategory = AppCategory.PRODUCTIVITY


@dataclass
class _SeqCounter:
    value: int = 0

    def next(self) -> int:
        v = self.value
        self.value += 1
        return v


def _sample_positive(
    rng: np.random.Generator, mean: float, std: float, floor: float = 1000.0
) -> float:
    return float(max(floor, rng.normal(mean, std)))


def generate_keyboard_stream(
    profile: SyntheticUserProfile,
    rng: np.random.Generator,
    *,
    n_keystrokes: int,
    t_start_us: int,
    seq: _SeqCounter,
    app_id: int = 1,
    device_class: DeviceClass = DeviceClass.INTERNAL_KEYBOARD,
) -> list[KeyboardEvent]:
    """Generate ``n_keystrokes`` deterministic KEY_DOWN/KEY_UP pairs."""
    events: list[KeyboardEvent] = []
    t_down = float(t_start_us)
    for _ in range(n_keystrokes):
        r = rng.random()
        if r < profile.backspace_rate:
            key_class = KeyClass.BACKSPACE
        elif r < profile.backspace_rate + profile.modifier_rate:
            key_class = KeyClass.MODIFIER
        elif r < profile.backspace_rate + profile.modifier_rate + profile.space_rate:
            key_class = KeyClass.SPACE
        else:
            key_class = _ALPHA_CLASSES[rng.integers(0, len(_ALPHA_CLASSES))]

        dwell = _sample_positive(rng, profile.mean_dwell_us, profile.std_dwell_us)
        t_up = t_down + dwell

        events.append(
            KeyboardEvent(
                type="KEY_DOWN",
                t_capture_us=int(t_down),
                key_class=key_class,
                is_repeat=False,
                device_class=device_class,
                app_id=app_id,
                seq=seq.next(),
            )
        )
        events.append(
            KeyboardEvent(
                type="KEY_UP",
                t_capture_us=int(t_up),
                key_class=key_class,
                is_repeat=False,
                device_class=device_class,
                app_id=app_id,
                seq=seq.next(),
            )
        )

        dd_latency = _sample_positive(rng, profile.mean_dd_latency_us, profile.std_dd_latency_us)
        t_down = t_down + dd_latency
    return events


def generate_mouse_stream(
    profile: SyntheticUserProfile,
    rng: np.random.Generator,
    *,
    duration_us: int,
    t_start_us: int,
    seq: _SeqCounter,
    app_id: int = 1,
    device_class: DeviceClass = DeviceClass.EXTERNAL_MOUSE,
) -> list[MouseEvent]:
    """Generate a deterministic mouse move/click/scroll stream for ``duration_us``."""
    events: list[MouseEvent] = []
    t = float(t_start_us)
    t_end = t_start_us + duration_us
    x, y = 500.0, 400.0
    next_click_t = t + rng.exponential(60_000_000.0 / max(profile.click_rate_per_min, 1e-6))
    next_scroll_t = t + rng.exponential(60_000_000.0 / max(profile.scroll_rate_per_min, 1e-6))

    while t < t_end:
        speed = _sample_positive(
            rng, profile.mean_mouse_speed_px_s, profile.std_mouse_speed_px_s, floor=0.0
        )
        angle = rng.uniform(0, 2 * math.pi)
        dt_s = profile.mouse_step_dt_us / 1_000_000.0
        dx = speed * dt_s * math.cos(angle)
        dy = speed * dt_s * math.sin(angle)
        x, y = x + dx, y + dy
        t += profile.mouse_step_dt_us

        events.append(
            MouseEvent(
                type="MOVE",
                t_capture_us=int(t),
                x=int(round(x)),
                y=int(round(y)),
                device_class=device_class,
                app_id=app_id,
                seq=seq.next(),
            )
        )

        if t >= next_click_t:
            events.append(
                MouseEvent(
                    type="BUTTON_DOWN",
                    t_capture_us=int(t),
                    x=int(round(x)),
                    y=int(round(y)),
                    button=MouseButton.LEFT,
                    device_class=device_class,
                    app_id=app_id,
                    seq=seq.next(),
                )
            )
            click_dur = _sample_positive(rng, 90_000.0, 20_000.0)
            events.append(
                MouseEvent(
                    type="BUTTON_UP",
                    t_capture_us=int(t + click_dur),
                    x=int(round(x)),
                    y=int(round(y)),
                    button=MouseButton.LEFT,
                    device_class=device_class,
                    app_id=app_id,
                    seq=seq.next(),
                )
            )
            next_click_t = t + rng.exponential(60_000_000.0 / max(profile.click_rate_per_min, 1e-6))

        if t >= next_scroll_t:
            events.append(
                MouseEvent(
                    type="SCROLL",
                    t_capture_us=int(t),
                    x=int(round(x)),
                    y=int(round(y)),
                    scroll_dy=int(rng.choice([-1, 1])),
                    device_class=device_class,
                    app_id=app_id,
                    seq=seq.next(),
                )
            )
            next_scroll_t = t + rng.exponential(
                60_000_000.0 / max(profile.scroll_rate_per_min, 1e-6)
            )

    return events


def generate_context_stream(
    profile: SyntheticUserProfile,
    rng: np.random.Generator,
    *,
    duration_us: int,
    t_start_us: int,
    seq: _SeqCounter,
    switch_rate_per_min: float = 2.0,
    app_id: int = 1,
) -> list[ContextEvent]:
    """Generate focus events biased toward the user's preferred category."""
    other_categories = [c for c in AppCategory if c != profile.preferred_app_category]
    events: list[ContextEvent] = []
    t = float(t_start_us)
    t_end = t_start_us + duration_us
    events.append(
        ContextEvent(
            t_capture_us=int(t),
            app_id=app_id,
            category=profile.preferred_app_category,
            seq=seq.next(),
        )
    )
    while True:
        gap = rng.exponential(60_000_000.0 / max(switch_rate_per_min, 1e-6))
        t += gap
        if t >= t_end:
            break
        category = (
            profile.preferred_app_category
            if rng.random() < 0.7
            else other_categories[rng.integers(0, len(other_categories))]
        )
        events.append(
            ContextEvent(t_capture_us=int(t), app_id=app_id, category=category, seq=seq.next())
        )
    return events


@dataclass
class SyntheticSegment:
    """One synthetic (session, segment) worth of events, ready for ``extract_windows``."""

    user_id: str
    session_id: str
    segment_id: str
    collection_day: str
    keyboard_events: list[KeyboardEvent]
    mouse_events: list[MouseEvent]
    context_events: list[ContextEvent]


def generate_segment(
    profile: SyntheticUserProfile,
    *,
    seed: int,
    session_id: str,
    segment_id: str,
    collection_day: str,
    duration_us: int,
    t_start_us: int = 0,
) -> SyntheticSegment:
    """Generate one deterministic segment's worth of multimodal events.

    Determinism: the same ``(profile, seed, duration_us, t_start_us)`` always
    produces byte-identical (structurally-equal) output, since generation
    uses a single seeded ``numpy.random.Generator`` and no other source of
    randomness or wall-clock time.
    """
    rng = np.random.default_rng(seed)
    seq = _SeqCounter()

    # Roughly one keystroke per mean_dd_latency; derive a keystroke budget
    # from the requested duration so segments of different lengths get
    # proportionally more/less typing, then generate and clip to duration.
    approx_keystrokes = max(1, int(duration_us / max(profile.mean_dd_latency_us, 1.0)))
    kbd_events = generate_keyboard_stream(
        profile, rng, n_keystrokes=approx_keystrokes, t_start_us=t_start_us, seq=seq
    )
    kbd_events = [e for e in kbd_events if e.t_capture_us <= t_start_us + duration_us]

    mouse_events = generate_mouse_stream(
        profile, rng, duration_us=duration_us, t_start_us=t_start_us, seq=seq
    )
    context_events = generate_context_stream(
        profile, rng, duration_us=duration_us, t_start_us=t_start_us, seq=seq
    )

    return SyntheticSegment(
        user_id=profile.user_id,
        session_id=session_id,
        segment_id=segment_id,
        collection_day=collection_day,
        keyboard_events=kbd_events,
        mouse_events=mouse_events,
        context_events=context_events,
    )


def generate_takeover_segment(
    genuine_profile: SyntheticUserProfile,
    impostor_profile: SyntheticUserProfile,
    *,
    seed: int,
    session_id: str,
    segment_id: str,
    collection_day: str,
    duration_us: int,
    takeover_fraction: float = 0.5,
    t_start_us: int = 0,
) -> SyntheticSegment:
    """Generate a segment where behavior switches from one profile to another partway through.

    Useful for validating risk-engine/decision-layer scenarios (PLAN.md
    Section 13.3 live hijack drill) mechanically before real hijack drills
    are run. This is explicitly a mechanics fixture, not evaluation evidence
    (PLAN.md Section 9.6).
    """
    split_us = int(duration_us * takeover_fraction)
    first = generate_segment(
        genuine_profile,
        seed=seed,
        session_id=session_id,
        segment_id=segment_id,
        collection_day=collection_day,
        duration_us=split_us,
        t_start_us=t_start_us,
    )
    second = generate_segment(
        impostor_profile,
        seed=seed + 1,
        session_id=session_id,
        segment_id=segment_id,
        collection_day=collection_day,
        duration_us=duration_us - split_us,
        t_start_us=t_start_us + split_us,
    )
    return SyntheticSegment(
        user_id=genuine_profile.user_id,
        session_id=session_id,
        segment_id=segment_id,
        collection_day=collection_day,
        keyboard_events=[*first.keyboard_events, *second.keyboard_events],
        mouse_events=[*first.mouse_events, *second.mouse_events],
        context_events=[*first.context_events, *second.context_events],
    )


def generate_multiday_corpus(
    profile: SyntheticUserProfile,
    *,
    base_seed: int,
    num_days: int,
    segments_per_day: int = 4,
    segment_duration_us: int = 30 * 60 * 1_000_000,  # 30 minutes
) -> list[SyntheticSegment]:
    """Generate a deterministic multi-day corpus for one user, for day-disjoint-split fixtures."""
    segments: list[SyntheticSegment] = []
    base_date = date(2026, 1, 1)
    for day_idx in range(num_days):
        collection_day = (base_date + timedelta(days=day_idx)).isoformat()
        for seg_idx in range(segments_per_day):
            seed = base_seed + day_idx * 1000 + seg_idx
            t_start = (
                day_idx * segments_per_day * segment_duration_us + seg_idx * segment_duration_us
            )
            segments.append(
                generate_segment(
                    profile,
                    seed=seed,
                    session_id=f"{profile.user_id}-day{day_idx}",
                    segment_id=f"{profile.user_id}-day{day_idx}-seg{seg_idx}",
                    collection_day=collection_day,
                    duration_us=segment_duration_us,
                    t_start_us=t_start,
                )
            )
    return segments
