"""Window closing (PLAN.md Section 7.3 / 2.1) and ADR-005 quality gating.

ASSUMPTION -- session/segment attribution: PLAN.md assigns session/segment
attribution to the ingestion service (T-007, owner Joel), which does not
exist yet. This module accepts already-attributed events for a single
(user, session, segment) at a time — callers (training pipeline, tests,
synthetic fixtures) are responsible for grouping events by segment before
calling ``WindowBuilder``/``extract_windows``. When T-007 lands, its output
should feed directly into this same entry point.
"""

from __future__ import annotations

import bisect
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from ml.features.keyboard import compute_keyboard_features
from ml.features.mouse import compute_mouse_features
from ml.features.schema import (
    AppCategory,
    ContextBlock,
    ContextEvent,
    DeviceClass,
    FeatureWindow,
    KeyboardEvent,
    KeyboardFeatures,
    MouseEvent,
    MouseFeatures,
    Provenance,
    QualityLabel,
)


@dataclass(frozen=True)
class WindowConfig:
    window_seconds: float
    window_keystrokes: int
    pause_threshold_us: int
    burst_gap_threshold_us: int
    mouse_segment_gap_us: int
    mouse_micro_pause_us: int
    double_click_max_gap_us: int
    scroll_burst_gap_us: int
    reference_screen_width_px: int
    reference_screen_height_px: int
    min_keystrokes: int
    min_mouse_samples: int

    @property
    def window_seconds_us(self) -> int:
        return int(self.window_seconds * 1_000_000)


class _ContextTimeline:
    """Precomputed focus-category intervals for O(log n) per-window lookup."""

    def __init__(self, context_events: Sequence[ContextEvent]):
        ordered = sorted(context_events, key=lambda e: (e.t_capture_us, e.seq))
        self._starts: list[int] = [e.t_capture_us for e in ordered]
        self._categories: list[AppCategory] = [e.category for e in ordered]

    def fractions_and_switches(
        self, t_start: int, t_end: int
    ) -> tuple[dict[AppCategory, float], int]:
        duration = max(t_end - t_start, 1)
        if not self._starts:
            return {AppCategory.UNKNOWN: 1.0}, 0

        # Category active at t_start: the last focus change at or before t_start.
        idx = bisect.bisect_right(self._starts, t_start) - 1
        cat = self._categories[idx] if idx >= 0 else AppCategory.UNKNOWN
        cursor = t_start

        totals: dict[AppCategory, float] = {}
        switches = 0
        j = idx + 1
        while j < len(self._starts) and self._starts[j] < t_end:
            seg_end = self._starts[j]
            if seg_end > cursor:
                totals[cat] = totals.get(cat, 0.0) + (seg_end - cursor)
            cat = self._categories[j]
            cursor = seg_end
            switches += 1
            j += 1
        if cursor < t_end:
            totals[cat] = totals.get(cat, 0.0) + (t_end - cursor)

        fractions = {k: v / duration for k, v in totals.items()}
        return fractions, switches


def _dominant_device_class(
    kbd: Sequence[KeyboardEvent], mouse: Sequence[MouseEvent]
) -> DeviceClass:
    counts: Counter[DeviceClass] = Counter()
    for keyboard_event in kbd:
        counts[keyboard_event.device_class] += 1
    for mouse_event in mouse:
        counts[mouse_event.device_class] += 1
    if not counts:
        return DeviceClass.UNKNOWN
    return counts.most_common(1)[0][0]


def _build_context_block(
    t_start: int,
    t_end: int,
    timeline: _ContextTimeline,
    kbd: Sequence[KeyboardEvent],
    mouse: Sequence[MouseEvent],
) -> ContextBlock:
    fractions, switches = timeline.fractions_and_switches(t_start, t_end)
    duration_minutes = max(t_end - t_start, 1) / 1_000_000.0 / 60.0
    dominant = max(fractions.items(), key=lambda kv: kv[1])[0] if fractions else AppCategory.UNKNOWN
    return ContextBlock(
        dominant_category=dominant,
        category_fractions={category.value: fraction for category, fraction in fractions.items()},
        app_switch_rate=(switches / duration_minutes) if duration_minutes > 0 else 0.0,
        device_class=_dominant_device_class(kbd, mouse),
    )


def _quality_label(key_count: int, mouse_count: int, cfg: WindowConfig) -> QualityLabel:
    kbd_ok = key_count >= cfg.min_keystrokes
    mouse_ok = mouse_count >= cfg.min_mouse_samples
    if kbd_ok and mouse_ok:
        return QualityLabel.FULL
    if kbd_ok:
        return QualityLabel.KBD_ONLY
    if mouse_ok:
        return QualityLabel.MOUSE_ONLY
    return QualityLabel.INSUFFICIENT_DATA


@dataclass
class _PendingWindow:
    t_start: int
    kbd: list[KeyboardEvent] = field(default_factory=list)
    mouse: list[MouseEvent] = field(default_factory=list)
    keystroke_count: int = 0


class WindowBuilder:
    """Closes windows on 30s-elapsed-or-100-keystrokes (PLAN.md Section 7.3).

    One instance processes one (user, session, segment) event stream.
    """

    def __init__(
        self,
        config: WindowConfig,
        *,
        user_id: str,
        session_id: str,
        segment_id: str,
        collection_day: str,
        provenance: Provenance,
        context_events: Sequence[ContextEvent] = (),
        device_resolution: tuple[int, int] | None = None,
        window_id_prefix: str = "",
    ):
        self.config = config
        self.user_id = user_id
        self.session_id = session_id
        self.segment_id = segment_id
        self.collection_day = collection_day
        self.provenance = provenance
        self.device_resolution = device_resolution
        self.window_id_prefix = window_id_prefix
        self._timeline = _ContextTimeline(context_events)
        self._pending: _PendingWindow | None = None
        self._window_index = 0

    def _close(self, t_end: int) -> FeatureWindow:
        assert self._pending is not None
        p = self._pending
        cfg = self.config

        key_count = p.keystroke_count
        mouse_count = len(p.mouse)
        label = _quality_label(key_count, mouse_count, cfg)

        kbd_features: KeyboardFeatures | None = None
        mouse_features: MouseFeatures | None = None
        if label in (QualityLabel.FULL, QualityLabel.KBD_ONLY):
            kbd_features = KeyboardFeatures(
                **compute_keyboard_features(
                    p.kbd,
                    pause_threshold_us=cfg.pause_threshold_us,
                    burst_gap_threshold_us=cfg.burst_gap_threshold_us,
                    window_duration_us=max(t_end - p.t_start, 1),
                )
            )
        if label in (QualityLabel.FULL, QualityLabel.MOUSE_ONLY):
            mouse_features = MouseFeatures(
                **compute_mouse_features(
                    p.mouse,
                    segment_gap_us=cfg.mouse_segment_gap_us,
                    micro_pause_us=cfg.mouse_micro_pause_us,
                    double_click_max_gap_us=cfg.double_click_max_gap_us,
                    scroll_burst_gap_us=cfg.scroll_burst_gap_us,
                    window_duration_us=max(t_end - p.t_start, 1),
                    reference_resolution=(
                        cfg.reference_screen_width_px,
                        cfg.reference_screen_height_px,
                    ),
                    device_resolution=self.device_resolution,
                )
            )

        context = _build_context_block(p.t_start, t_end, self._timeline, p.kbd, p.mouse)

        window = FeatureWindow(
            user_id=self.user_id,
            session_id=self.session_id,
            segment_id=self.segment_id,
            window_id=f"{self.window_id_prefix}{self.session_id}:{self.segment_id}:{self._window_index}",
            t_start_us=p.t_start,
            t_end_us=t_end,
            quality_label=label,
            key_event_count=key_count,
            mouse_event_count=mouse_count,
            collection_day=self.collection_day,
            provenance=self.provenance,
            keyboard_features=kbd_features,
            mouse_features=mouse_features,
            context=context,
        )
        self._window_index += 1
        self._pending = None
        return window

    def push_events(self, events: Sequence[KeyboardEvent | MouseEvent]) -> list[FeatureWindow]:
        """Feed a time-ordered mixed stream of keyboard/mouse events.

        Returns any windows that closed as a result of this call. Callers
        should invoke ``flush()`` at end-of-segment to emit the trailing
        partial window, if any.
        """
        closed: list[FeatureWindow] = []
        ordered = sorted(events, key=lambda e: (e.t_capture_us, e.seq))
        for ev in ordered:
            if self._pending is None:
                self._pending = _PendingWindow(t_start=ev.t_capture_us)

            if isinstance(ev, KeyboardEvent):
                self._pending.kbd.append(ev)
                if ev.type == "KEY_DOWN" and not ev.is_repeat:
                    self._pending.keystroke_count += 1
            else:
                self._pending.mouse.append(ev)

            elapsed = ev.t_capture_us - self._pending.t_start
            if (
                self._pending.keystroke_count >= self.config.window_keystrokes
                or elapsed >= self.config.window_seconds_us
            ):
                closed.append(self._close(ev.t_capture_us))
        return closed

    def flush(self) -> FeatureWindow | None:
        """Close and return the trailing partial window, if any events are pending."""
        if self._pending is None:
            return None
        last_t = max(
            [e.t_capture_us for e in self._pending.kbd]
            + [e.t_capture_us for e in self._pending.mouse]
        )
        return self._close(last_t)
