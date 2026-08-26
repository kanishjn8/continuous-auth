from __future__ import annotations

import math

from ml.features.keyboard import KEYBOARD_FEATURE_NAMES, compute_keyboard_features
from ml.features.schema import KeyClass
from ml.tests.conftest import kbd


def test_empty_events_returns_zeroed_complete_block():
    result = compute_keyboard_features(
        [], pause_threshold_us=2_000_000, burst_gap_threshold_us=400_000, window_duration_us=30_000_000
    )
    assert set(result.keys()) == set(KEYBOARD_FEATURE_NAMES)
    assert all(v == 0.0 for v in result.values())
    assert all(math.isfinite(v) for v in result.values())


def test_single_matched_key_dwell_is_exact():
    events = [
        kbd(0, 1_000_000, KeyClass.ALPHA_L_HOME, "KEY_DOWN"),
        kbd(1, 1_100_000, KeyClass.ALPHA_L_HOME, "KEY_UP"),
    ]
    result = compute_keyboard_features(
        events, pause_threshold_us=2_000_000, burst_gap_threshold_us=400_000, window_duration_us=1_200_000
    )
    assert result["dwell_mean"] == 100_000.0
    assert result["dwell_median"] == 100_000.0
    assert result["dwell_std"] == 0.0


def test_typing_rate_matches_keystroke_count_over_duration():
    events = [
        kbd(0, 0, KeyClass.ALPHA_L_HOME, "KEY_DOWN"),
        kbd(1, 100_000, KeyClass.ALPHA_L_HOME, "KEY_UP"),
        kbd(2, 200_000, KeyClass.ALPHA_R_HOME, "KEY_DOWN"),
        kbd(3, 300_000, KeyClass.ALPHA_R_HOME, "KEY_UP"),
    ]
    result = compute_keyboard_features(
        events, pause_threshold_us=2_000_000, burst_gap_threshold_us=400_000, window_duration_us=1_000_000
    )
    # 2 non-repeat KEY_DOWN events over a 1.0s window.
    assert result["typing_rate"] == 2.0


def test_repeat_keydowns_excluded_from_keystroke_count():
    events = [
        kbd(0, 0, KeyClass.ALPHA_L_HOME, "KEY_DOWN"),
        kbd(1, 50_000, KeyClass.ALPHA_L_HOME, "KEY_DOWN", is_repeat=True),
        kbd(2, 100_000, KeyClass.ALPHA_L_HOME, "KEY_DOWN", is_repeat=True),
        kbd(3, 150_000, KeyClass.ALPHA_L_HOME, "KEY_UP"),
    ]
    result = compute_keyboard_features(
        events, pause_threshold_us=2_000_000, burst_gap_threshold_us=400_000, window_duration_us=1_000_000
    )
    assert result["typing_rate"] == 1.0


def test_backspace_ratio_and_correction_burst_rate():
    events = [
        kbd(0, 0, KeyClass.ALPHA_L_HOME, "KEY_DOWN"),
        kbd(1, 100_000, KeyClass.ALPHA_L_HOME, "KEY_UP"),
        kbd(2, 200_000, KeyClass.BACKSPACE, "KEY_DOWN"),
        kbd(3, 250_000, KeyClass.BACKSPACE, "KEY_UP"),
        kbd(4, 300_000, KeyClass.BACKSPACE, "KEY_DOWN"),
        kbd(5, 350_000, KeyClass.BACKSPACE, "KEY_UP"),
    ]
    result = compute_keyboard_features(
        events, pause_threshold_us=2_000_000, burst_gap_threshold_us=400_000, window_duration_us=60_000_000
    )
    assert result["backspace_ratio"] == 2 / 3
    # Two consecutive backspaces form exactly one correction burst (run length 2)
    # within a 60s window -> 1 burst/minute.
    assert result["correction_burst_rate"] == 1.0


def test_rollover_detected_when_second_key_pressed_before_first_released():
    events = [
        kbd(0, 0, KeyClass.ALPHA_L_HOME, "KEY_DOWN"),
        kbd(1, 50_000, KeyClass.ALPHA_R_HOME, "KEY_DOWN"),  # pressed before key 0 released
        kbd(2, 100_000, KeyClass.ALPHA_L_HOME, "KEY_UP"),
        kbd(3, 150_000, KeyClass.ALPHA_R_HOME, "KEY_UP"),
    ]
    result = compute_keyboard_features(
        events, pause_threshold_us=2_000_000, burst_gap_threshold_us=400_000, window_duration_us=200_000
    )
    assert result["rollover_ratio"] == 1.0


def test_no_rollover_for_sequential_non_overlapping_keys():
    events = [
        kbd(0, 0, KeyClass.ALPHA_L_HOME, "KEY_DOWN"),
        kbd(1, 50_000, KeyClass.ALPHA_L_HOME, "KEY_UP"),
        kbd(2, 100_000, KeyClass.ALPHA_R_HOME, "KEY_DOWN"),
        kbd(3, 150_000, KeyClass.ALPHA_R_HOME, "KEY_UP"),
    ]
    result = compute_keyboard_features(
        events, pause_threshold_us=2_000_000, burst_gap_threshold_us=400_000, window_duration_us=200_000
    )
    assert result["rollover_ratio"] == 0.0


def test_cross_hand_vs_same_hand_vs_same_row_transition_classification():
    # L-home -> R-home: cross-hand
    # R-home -> R-upper: same-hand, different row
    # R-upper -> R-upper (via another down): same-row
    events = [
        kbd(0, 0, KeyClass.ALPHA_L_HOME, "KEY_DOWN"),
        kbd(1, 10_000, KeyClass.ALPHA_L_HOME, "KEY_UP"),
        kbd(2, 100_000, KeyClass.ALPHA_R_HOME, "KEY_DOWN"),
        kbd(3, 110_000, KeyClass.ALPHA_R_HOME, "KEY_UP"),
        kbd(4, 200_000, KeyClass.ALPHA_R_UPPER, "KEY_DOWN"),
        kbd(5, 210_000, KeyClass.ALPHA_R_UPPER, "KEY_UP"),
        kbd(6, 300_000, KeyClass.ALPHA_R_UPPER, "KEY_DOWN"),
        kbd(7, 310_000, KeyClass.ALPHA_R_UPPER, "KEY_UP"),
    ]
    result = compute_keyboard_features(
        events, pause_threshold_us=2_000_000, burst_gap_threshold_us=400_000, window_duration_us=400_000
    )
    assert result["xhand_latency_mean"] == 100_000.0
    assert result["samehand_latency_mean"] == 100_000.0
    assert result["samerow_latency_mean"] == 100_000.0


def test_non_alphabetic_transitions_excluded_from_hand_row_buckets():
    events = [
        kbd(0, 0, KeyClass.DIGIT, "KEY_DOWN"),
        kbd(1, 10_000, KeyClass.DIGIT, "KEY_UP"),
        kbd(2, 100_000, KeyClass.ALPHA_L_HOME, "KEY_DOWN"),
        kbd(3, 110_000, KeyClass.ALPHA_L_HOME, "KEY_UP"),
    ]
    result = compute_keyboard_features(
        events, pause_threshold_us=2_000_000, burst_gap_threshold_us=400_000, window_duration_us=200_000
    )
    assert result["xhand_latency_mean"] == 0.0
    assert result["samehand_latency_mean"] == 0.0
    assert result["samerow_latency_mean"] == 0.0


def test_homerow_dwell_ratio():
    events = [
        kbd(0, 0, KeyClass.ALPHA_L_HOME, "KEY_DOWN"),
        kbd(1, 100_000, KeyClass.ALPHA_L_HOME, "KEY_UP"),  # 100ms dwell, home row
        kbd(2, 200_000, KeyClass.ALPHA_L_UPPER, "KEY_DOWN"),
        kbd(3, 300_000, KeyClass.ALPHA_L_UPPER, "KEY_UP"),  # 100ms dwell, not home row
    ]
    result = compute_keyboard_features(
        events, pause_threshold_us=2_000_000, burst_gap_threshold_us=400_000, window_duration_us=400_000
    )
    assert result["homerow_dwell_ratio"] == 0.5


def test_determinism_same_input_same_output():
    events = [
        kbd(0, 0, KeyClass.ALPHA_L_HOME, "KEY_DOWN"),
        kbd(1, 90_000, KeyClass.ALPHA_L_HOME, "KEY_UP"),
        kbd(2, 250_000, KeyClass.PUNCT, "KEY_DOWN"),
        kbd(3, 300_000, KeyClass.PUNCT, "KEY_UP"),
    ]
    r1 = compute_keyboard_features(
        events, pause_threshold_us=2_000_000, burst_gap_threshold_us=400_000, window_duration_us=400_000
    )
    r2 = compute_keyboard_features(
        list(reversed(events)),
        pause_threshold_us=2_000_000,
        burst_gap_threshold_us=400_000,
        window_duration_us=400_000,
    )
    assert r1 == r2


def test_no_nan_or_inf_on_degenerate_single_event_window():
    events = [kbd(0, 0, KeyClass.ALPHA_L_HOME, "KEY_DOWN")]  # unmatched key-down only
    result = compute_keyboard_features(
        events, pause_threshold_us=2_000_000, burst_gap_threshold_us=400_000, window_duration_us=1_000_000
    )
    assert all(math.isfinite(v) for v in result.values())
