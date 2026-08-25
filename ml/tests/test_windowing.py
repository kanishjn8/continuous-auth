from __future__ import annotations

from ml.features.extractor import extract_windows
from ml.features.schema import KeyClass, Provenance, QualityLabel
from ml.tests.conftest import kbd, mouse_move


def test_window_closes_on_keystroke_count(ml_config):
    events = [
        kbd(i, i * 100_000, KeyClass.ALPHA_L_HOME, "KEY_DOWN") for i in range(ml_config.windowing.window_keystrokes)
    ]
    windows = extract_windows(
        events,
        [],
        [],
        user_id="u1",
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-08-25",
        provenance=Provenance.SYNTHETIC,
        config=ml_config,
    )
    assert len(windows) == 1
    assert windows[0].key_event_count == ml_config.windowing.window_keystrokes


def test_window_closes_on_elapsed_time_even_with_few_keystrokes(ml_config):
    window_us = int(ml_config.windowing.window_seconds * 1_000_000)
    events = [
        kbd(0, 0, KeyClass.ALPHA_L_HOME, "KEY_DOWN"),
        kbd(1, window_us, KeyClass.ALPHA_R_HOME, "KEY_DOWN"),
    ]
    windows = extract_windows(
        events,
        [],
        [],
        user_id="u1",
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-08-25",
        provenance=Provenance.SYNTHETIC,
        config=ml_config,
    )
    assert len(windows) == 1
    assert windows[0].t_end_us - windows[0].t_start_us == window_us


def test_insufficient_data_window_has_no_fabricated_feature_vectors(ml_config):
    events = [kbd(0, 0, KeyClass.ALPHA_L_HOME, "KEY_DOWN")]  # far below min_keystrokes
    windows = extract_windows(
        events,
        [],
        [],
        user_id="u1",
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-08-25",
        provenance=Provenance.SYNTHETIC,
        config=ml_config,
    )
    assert len(windows) == 1
    assert windows[0].quality_label == QualityLabel.INSUFFICIENT_DATA
    assert windows[0].keyboard_features is None
    assert windows[0].mouse_features is None


def test_kbd_only_window_has_no_mouse_features(ml_config):
    n = ml_config.quality_gate.min_keystrokes + 2
    events = [kbd(i, i * 10_000, KeyClass.ALPHA_L_HOME, "KEY_DOWN") for i in range(n)]
    windows = extract_windows(
        events,
        [],
        [],
        user_id="u1",
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-08-25",
        provenance=Provenance.SYNTHETIC,
        config=ml_config,
    )
    assert windows[0].quality_label == QualityLabel.KBD_ONLY
    assert windows[0].keyboard_features is not None
    assert windows[0].mouse_features is None


def test_mouse_only_window_has_no_keyboard_features(ml_config):
    n = ml_config.quality_gate.min_mouse_samples + 2
    events = [mouse_move(i, i * 10_000, float(i), 0.0) for i in range(n)]
    windows = extract_windows(
        [],
        events,
        [],
        user_id="u1",
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-08-25",
        provenance=Provenance.SYNTHETIC,
        config=ml_config,
    )
    assert windows[0].quality_label == QualityLabel.MOUSE_ONLY
    assert windows[0].mouse_features is not None
    assert windows[0].keyboard_features is None


def test_full_window_has_both_feature_blocks(ml_config):
    kbd_n = ml_config.quality_gate.min_keystrokes + 2
    mouse_n = ml_config.quality_gate.min_mouse_samples + 2
    kbd_events = [kbd(i, i * 10_000, KeyClass.ALPHA_L_HOME, "KEY_DOWN") for i in range(kbd_n)]
    mouse_events = [mouse_move(1000 + i, i * 10_000, float(i), 0.0) for i in range(mouse_n)]
    windows = extract_windows(
        kbd_events,
        mouse_events,
        [],
        user_id="u1",
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-08-25",
        provenance=Provenance.SYNTHETIC,
        config=ml_config,
    )
    assert windows[0].quality_label == QualityLabel.FULL
    assert windows[0].keyboard_features is not None
    assert windows[0].mouse_features is not None


def test_context_never_appears_in_identity_feature_arrays(ml_config):
    kbd_n = ml_config.quality_gate.min_keystrokes + 2
    kbd_events = [kbd(i, i * 10_000, KeyClass.ALPHA_L_HOME, "KEY_DOWN") for i in range(kbd_n)]
    windows = extract_windows(
        kbd_events,
        [],
        [],
        user_id="u1",
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-08-25",
        provenance=Provenance.SYNTHETIC,
        config=ml_config,
    )
    kbd_features, mouse_features = windows[0].to_identity_arrays()
    context_field_names = {"dominant_category", "category_fractions", "app_switch_rate", "device_class"}
    assert context_field_names.isdisjoint((kbd_features or {}).keys())
    assert context_field_names.isdisjoint((mouse_features or {}).keys())


def test_determinism_same_input_same_windows(ml_config):
    kbd_n = ml_config.quality_gate.min_keystrokes + 2
    kbd_events = [kbd(i, i * 10_000, KeyClass.ALPHA_L_HOME, "KEY_DOWN") for i in range(kbd_n)]
    kwargs = dict(
        keyboard_events=kbd_events,
        mouse_events=[],
        context_events=[],
        user_id="u1",
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-08-25",
        provenance=Provenance.SYNTHETIC,
        config=ml_config,
    )
    w1 = extract_windows(**kwargs)
    w2 = extract_windows(**kwargs)
    assert [w.model_dump() for w in w1] == [w.model_dump() for w in w2]


def test_trailing_partial_window_is_not_dropped(ml_config):
    # Fewer events than either close condition -> must still emit one window via flush().
    events = [kbd(0, 0, KeyClass.ALPHA_L_HOME, "KEY_DOWN")]
    windows = extract_windows(
        events,
        [],
        [],
        user_id="u1",
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-08-25",
        provenance=Provenance.SYNTHETIC,
        config=ml_config,
    )
    assert len(windows) == 1
