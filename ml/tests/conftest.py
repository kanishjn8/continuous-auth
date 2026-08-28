from __future__ import annotations

import pytest

from ml.features.config import load_config
from ml.features.schema import (
    DeviceClass,
    KeyboardEvent,
    KeyClass,
    MouseButton,
    MouseEvent,
)


def kbd(
    seq: int, t_us: int, key_class: KeyClass, ev_type: str, *, is_repeat: bool = False
) -> KeyboardEvent:
    return KeyboardEvent(
        type=ev_type,
        t_capture_us=t_us,
        key_class=key_class,
        is_repeat=is_repeat,
        device_class=DeviceClass.INTERNAL_KEYBOARD,
        app_id=1,
        seq=seq,
    )


def mouse_move(seq: int, t_us: int, x: float, y: float) -> MouseEvent:
    return MouseEvent(
        type="MOVE",
        t_capture_us=t_us,
        x=x,
        y=y,
        device_class=DeviceClass.EXTERNAL_MOUSE,
        app_id=1,
        seq=seq,
    )


def mouse_click(
    seq: int, t_us: int, ev_type: str, button: MouseButton = MouseButton.LEFT
) -> MouseEvent:
    return MouseEvent(
        type=ev_type,
        t_capture_us=t_us,
        x=0.0,
        y=0.0,
        button=button,
        device_class=DeviceClass.EXTERNAL_MOUSE,
        app_id=1,
        seq=seq,
    )


def mouse_scroll(seq: int, t_us: int, dy: float = -1.0) -> MouseEvent:
    return MouseEvent(
        type="SCROLL",
        t_capture_us=t_us,
        x=0.0,
        y=0.0,
        scroll_dy=dy,
        device_class=DeviceClass.EXTERNAL_MOUSE,
        app_id=1,
        seq=seq,
    )


@pytest.fixture()
def ml_config():
    return load_config()


def generate_user_windows(
    user_id: str, seed: int, config, *, duration_minutes: int = 60, **profile_kwargs
):
    """Test helper: synthesize enough FeatureWindows for one user to exceed
    typical min_baseline_windows thresholds, via the real T-008 pipeline.
    """
    from ml.datasets.synthetic import SyntheticUserProfile, generate_segment
    from ml.features.extractor import extract_windows
    from ml.features.schema import Provenance

    profile = SyntheticUserProfile(user_id=user_id, **profile_kwargs)
    seg = generate_segment(
        profile,
        seed=seed,
        session_id=f"{user_id}-s1",
        segment_id=f"{user_id}-seg1",
        collection_day="2026-01-01",
        duration_us=duration_minutes * 60 * 1_000_000,
    )
    return extract_windows(
        seg.keyboard_events,
        seg.mouse_events,
        seg.context_events,
        user_id=user_id,
        session_id=seg.session_id,
        segment_id=seg.segment_id,
        collection_day=seg.collection_day,
        provenance=Provenance.SYNTHETIC,
        config=config,
    )


def generate_multiday_user_windows(
    user_id: str,
    base_seed: int,
    config,
    *,
    num_days: int = 6,
    segments_per_day: int = 2,
    segment_minutes: int = 20,
    **profile_kwargs,
):
    """Test helper: synthesize a multi-day corpus for one user, run through
    the real T-008 pipeline, for day-disjoint-split evaluation fixtures.
    """
    from ml.datasets.synthetic import SyntheticUserProfile, generate_multiday_corpus
    from ml.features.extractor import extract_windows
    from ml.features.schema import Provenance

    profile = SyntheticUserProfile(user_id=user_id, **profile_kwargs)
    segments = generate_multiday_corpus(
        profile,
        base_seed=base_seed,
        num_days=num_days,
        segments_per_day=segments_per_day,
        segment_duration_us=segment_minutes * 60 * 1_000_000,
    )
    windows = []
    for seg in segments:
        windows.extend(
            extract_windows(
                seg.keyboard_events,
                seg.mouse_events,
                seg.context_events,
                user_id=user_id,
                session_id=seg.session_id,
                segment_id=seg.segment_id,
                collection_day=seg.collection_day,
                provenance=Provenance.SYNTHETIC,
                config=config,
            )
        )
    return windows
