from __future__ import annotations

import math

from ml.features.mouse import MOUSE_FEATURE_NAMES, compute_mouse_features
from ml.features.schema import MouseButton
from ml.tests.conftest import mouse_click, mouse_move, mouse_scroll

DEFAULT_KW = dict(
    segment_gap_us=500_000,
    micro_pause_us=150_000,
    double_click_max_gap_us=500_000,
    scroll_burst_gap_us=300_000,
    reference_resolution=(1920, 1080),
)


def test_empty_events_returns_zeroed_complete_block():
    result = compute_mouse_features([], window_duration_us=30_000_000, **DEFAULT_KW)
    assert set(result.keys()) == set(MOUSE_FEATURE_NAMES)
    assert all(v == 0.0 for v in result.values())
    assert all(math.isfinite(v) for v in result.values())


def test_velocity_exact_for_uniform_straight_line_motion():
    # 100px in x every 100ms => 1000 px/s constant velocity, dt=100ms steps.
    events = [
        mouse_move(0, 0, 0.0, 0.0),
        mouse_move(1, 100_000, 100.0, 0.0),
        mouse_move(2, 200_000, 200.0, 0.0),
        mouse_move(3, 300_000, 300.0, 0.0),
    ]
    result = compute_mouse_features(events, window_duration_us=300_000, **DEFAULT_KW)
    assert math.isclose(result["velocity_mean"], 1000.0, rel_tol=1e-9)
    assert math.isclose(result["velocity_max"], 1000.0, rel_tol=1e-9)
    assert result["velocity_std"] == 0.0


def test_straightness_ratio_is_one_for_straight_line():
    events = [
        mouse_move(0, 0, 0.0, 0.0),
        mouse_move(1, 100_000, 50.0, 0.0),
        mouse_move(2, 200_000, 100.0, 0.0),
    ]
    result = compute_mouse_features(events, window_duration_us=200_000, **DEFAULT_KW)
    assert math.isclose(result["straightness_ratio"], 1.0, rel_tol=1e-9)


def test_straightness_ratio_less_than_one_for_bent_path():
    events = [
        mouse_move(0, 0, 0.0, 0.0),
        mouse_move(1, 100_000, 100.0, 0.0),
        mouse_move(2, 200_000, 100.0, 100.0),
    ]
    result = compute_mouse_features(events, window_duration_us=200_000, **DEFAULT_KW)
    assert result["straightness_ratio"] < 1.0


def test_click_duration_exact():
    events = [
        mouse_click(0, 0, "BUTTON_DOWN"),
        mouse_click(1, 120_000, "BUTTON_UP"),
    ]
    result = compute_mouse_features(events, window_duration_us=120_000, **DEFAULT_KW)
    assert result["click_duration_mean"] == 120_000.0
    assert math.isclose(result["click_rate"], 500.0, rel_tol=1e-9)  # 1 click / 0.12s -> 500/min


def test_straightness_ratio_is_zero_for_zero_path_length_segment():
    # Every point coincides (pure jitter, no net movement): straight_dist
    # and seg_path_length are both 0, so the ratio is undefined -- must
    # fall back to 0.0 like every other degenerate statistic in this
    # module, not 1.0 ("perfectly straight"), which would be backwards for
    # a segment that never actually moved.
    events = [
        mouse_move(0, 0, 50.0, 50.0),
        mouse_move(1, 100_000, 50.0, 50.0),
        mouse_move(2, 200_000, 50.0, 50.0),
    ]
    result = compute_mouse_features(events, window_duration_us=200_000, **DEFAULT_KW)
    assert result["straightness_ratio"] == 0.0


def test_double_click_interval_detected_within_threshold():
    events = [
        mouse_click(0, 0, "BUTTON_DOWN"),
        mouse_click(1, 50_000, "BUTTON_UP"),
        mouse_click(2, 200_000, "BUTTON_DOWN"),  # 200ms after first down, within 500ms threshold
        mouse_click(3, 250_000, "BUTTON_UP"),
    ]
    result = compute_mouse_features(events, window_duration_us=250_000, **DEFAULT_KW)
    assert result["double_click_interval_mean"] == 200_000.0


def test_double_click_not_detected_beyond_threshold():
    events = [
        mouse_click(0, 0, "BUTTON_DOWN"),
        mouse_click(1, 50_000, "BUTTON_UP"),
        mouse_click(2, 800_000, "BUTTON_DOWN"),  # 800ms > 500ms threshold
        mouse_click(3, 850_000, "BUTTON_UP"),
    ]
    result = compute_mouse_features(events, window_duration_us=850_000, **DEFAULT_KW)
    assert result["double_click_interval_mean"] == 0.0


def test_different_buttons_not_matched_for_click_duration():
    events = [
        mouse_click(0, 0, "BUTTON_DOWN", button=MouseButton.LEFT),
        mouse_click(1, 100_000, "BUTTON_UP", button=MouseButton.RIGHT),
    ]
    result = compute_mouse_features(events, window_duration_us=100_000, **DEFAULT_KW)
    assert result["click_duration_mean"] == 0.0


def test_scroll_rate_and_burst():
    events = [
        mouse_scroll(0, 0),
        mouse_scroll(1, 100_000),
        mouse_scroll(2, 200_000),
        mouse_scroll(3, 5_000_000),  # isolated, separate burst
    ]
    result = compute_mouse_features(events, window_duration_us=5_000_000, **DEFAULT_KW)
    assert math.isclose(result["scroll_rate"], 48.0, rel_tol=1e-9)  # 4 scrolls / 5s -> 48/min
    assert result["scroll_burst_mean"] == (3 + 1) / 2  # one burst of 3, one burst of 1


def test_move_to_click_ratio():
    events = [
        mouse_move(0, 0, 0.0, 0.0),
        mouse_move(1, 100_000, 10.0, 0.0),
        mouse_move(2, 200_000, 20.0, 0.0),
        mouse_click(3, 300_000, "BUTTON_DOWN"),
        mouse_click(4, 350_000, "BUTTON_UP"),
    ]
    result = compute_mouse_features(events, window_duration_us=350_000, **DEFAULT_KW)
    assert result["move_to_click_ratio"] == 3.0


def test_move_to_click_ratio_is_zero_when_no_clicks():
    # click_count == 0 makes the ratio undefined (would-be division by
    # zero) -- must fall back to 0.0 like every other degenerate statistic
    # in this module, not the raw (unbounded, scale-inconsistent) move
    # count.
    events = [
        mouse_move(0, 0, 0.0, 0.0),
        mouse_move(1, 100_000, 10.0, 0.0),
        mouse_move(2, 200_000, 20.0, 0.0),
    ]
    result = compute_mouse_features(events, window_duration_us=200_000, **DEFAULT_KW)
    assert result["move_to_click_ratio"] == 0.0


def test_resolution_normalization_scales_velocity():
    events = [
        mouse_move(0, 0, 0.0, 0.0),
        mouse_move(1, 100_000, 100.0, 0.0),
    ]
    unnormalized = compute_mouse_features(
        events, window_duration_us=100_000, device_resolution=None, **DEFAULT_KW
    )
    normalized = compute_mouse_features(
        events, window_duration_us=100_000, device_resolution=(3840, 1080), **DEFAULT_KW
    )
    # Reference width 1920 vs device width 3840 => scale factor 0.5
    assert math.isclose(
        normalized["velocity_mean"], unnormalized["velocity_mean"] * 0.5, rel_tol=1e-9
    )


def test_jerk_not_computed_across_segment_boundary():
    # Two segments, each internally constant-velocity (zero jerk within the
    # segment), separated by a gap far exceeding segment_gap_us. A jerk
    # computation that incorrectly bridges segments would see a large,
    # spurious acceleration delta between the last sample of segment 1 and
    # the first of segment 2; the correct answer is jerk_mean == 0.0.
    events = [
        mouse_move(0, 0, 0.0, 0.0),
        mouse_move(1, 100_000, 100.0, 0.0),
        mouse_move(2, 200_000, 200.0, 0.0),
        # gap of 2_000_000us >> segment_gap_us(500_000) -> new segment
        mouse_move(3, 2_200_000, 200.0, 500.0),
        mouse_move(4, 2_300_000, 200.0, 1000.0),
        mouse_move(5, 2_400_000, 200.0, 1500.0),
    ]
    result = compute_mouse_features(events, window_duration_us=2_400_000, **DEFAULT_KW)
    assert result["jerk_mean"] == 0.0


def test_no_nan_or_inf_on_degenerate_single_event_window():
    events = [mouse_move(0, 0, 0.0, 0.0)]
    result = compute_mouse_features(events, window_duration_us=1_000_000, **DEFAULT_KW)
    assert all(math.isfinite(v) for v in result.values())


def test_determinism_same_input_same_output():
    events = [
        mouse_move(0, 0, 0.0, 0.0),
        mouse_move(1, 100_000, 20.0, 5.0),
        mouse_click(2, 150_000, "BUTTON_DOWN"),
        mouse_click(3, 200_000, "BUTTON_UP"),
    ]
    r1 = compute_mouse_features(events, window_duration_us=200_000, **DEFAULT_KW)
    r2 = compute_mouse_features(list(events), window_duration_us=200_000, **DEFAULT_KW)
    assert r1 == r2
