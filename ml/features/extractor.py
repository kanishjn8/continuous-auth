"""High-level entry point: raw events -> list[FeatureWindow] (T-008).

This is the function both the synthetic/public-dataset training fixtures
(``ml/datasets``) and, eventually, a live-inference adapter should call —
per the T-008 acceptance criterion that training and inference share one
feature implementation.
"""

from __future__ import annotations

from typing import Sequence

from ml.features.config import MLConfig
from ml.features.schema import ContextEvent, FeatureWindow, KeyboardEvent, MouseEvent, Provenance
from ml.features.windowing import WindowBuilder, WindowConfig


def window_config_from_ml_config(config: MLConfig) -> WindowConfig:
    return WindowConfig(
        window_seconds=config.windowing.window_seconds,
        window_keystrokes=config.windowing.window_keystrokes,
        pause_threshold_us=config.feature_computation.pause_threshold_us,
        burst_gap_threshold_us=config.feature_computation.burst_gap_threshold_us,
        mouse_segment_gap_us=config.feature_computation.mouse_segment_gap_us,
        mouse_micro_pause_us=config.feature_computation.mouse_micro_pause_us,
        double_click_max_gap_us=config.feature_computation.double_click_max_gap_us,
        scroll_burst_gap_us=config.feature_computation.scroll_burst_gap_us,
        reference_screen_width_px=config.feature_computation.reference_screen_width_px,
        reference_screen_height_px=config.feature_computation.reference_screen_height_px,
        min_keystrokes=config.quality_gate.min_keystrokes,
        min_mouse_samples=config.quality_gate.min_mouse_samples,
    )


def extract_windows(
    keyboard_events: Sequence[KeyboardEvent],
    mouse_events: Sequence[MouseEvent],
    context_events: Sequence[ContextEvent],
    *,
    user_id: str,
    session_id: str,
    segment_id: str,
    collection_day: str,
    provenance: Provenance,
    config: MLConfig,
    device_resolution: tuple[int, int] | None = None,
) -> list[FeatureWindow]:
    """Extract all feature windows for one (user, session, segment) event stream."""
    window_cfg = window_config_from_ml_config(config)
    builder = WindowBuilder(
        window_cfg,
        user_id=user_id,
        session_id=session_id,
        segment_id=segment_id,
        collection_day=collection_day,
        provenance=provenance,
        context_events=context_events,
        device_resolution=device_resolution,
    )
    windows = builder.push_events([*keyboard_events, *mouse_events])
    trailing = builder.flush()
    if trailing is not None:
        windows.append(trailing)
    return windows
