from __future__ import annotations

import pytest

from ml.datasets.public_keystroke import (
    classify_char_to_key_class,
    load_public_keystroke_csv,
    write_fixture_csv,
)
from ml.features.extractor import extract_windows
from ml.features.schema import KeyClass, Provenance, QualityLabel


def test_classify_char_maps_known_classes():
    assert classify_char_to_key_class("a") == KeyClass.ALPHA_L_HOME
    assert classify_char_to_key_class("j") == KeyClass.ALPHA_R_HOME
    assert classify_char_to_key_class("q") == KeyClass.ALPHA_L_UPPER
    assert classify_char_to_key_class("p") == KeyClass.ALPHA_R_UPPER
    assert classify_char_to_key_class("z") == KeyClass.ALPHA_L_LOWER
    assert classify_char_to_key_class("m") == KeyClass.ALPHA_R_LOWER
    assert classify_char_to_key_class("5") == KeyClass.DIGIT
    assert classify_char_to_key_class(" ") == KeyClass.SPACE
    assert classify_char_to_key_class("Backspace") == KeyClass.BACKSPACE
    assert classify_char_to_key_class("Enter") == KeyClass.ENTER
    assert classify_char_to_key_class("Shift") == KeyClass.MODIFIER
    assert classify_char_to_key_class("F5") == KeyClass.FUNCTION
    assert classify_char_to_key_class("!") == KeyClass.PUNCT
    assert classify_char_to_key_class("") == KeyClass.OTHER


def test_classify_is_case_insensitive_for_letters():
    # Physical key position is identical for 'A' and 'a'; only the class
    # (not the letter) is ever retained.
    assert classify_char_to_key_class("A") == classify_char_to_key_class("a")


def test_loader_round_trip_produces_valid_keyboard_events(tmp_path):
    csv_path = tmp_path / "fixture.csv"
    write_fixture_csv(
        csv_path,
        [
            ("u1", 0.0, 80.0, "h"),
            ("u1", 200.0, 280.0, "e"),
            ("u1", 400.0, 470.0, "l"),
            ("u1", 600.0, 690.0, "l"),
            ("u1", 800.0, 860.0, "o"),
            ("u2", 0.0, 90.0, "w"),
            ("u2", 250.0, 330.0, "o"),
        ],
    )
    result = load_public_keystroke_csv(csv_path)
    assert set(result.keys()) == {"u1", "u2"}
    assert len(result["u1"]) == 10  # 5 keystrokes * (KEY_DOWN + KEY_UP)
    assert len(result["u2"]) == 4

    events = result["u1"]
    assert events[0].type == "KEY_DOWN"
    assert events[1].type == "KEY_UP"
    # ms -> us conversion applied.
    assert events[0].t_capture_us == 0
    assert events[1].t_capture_us == 80_000


def test_loader_rejects_missing_columns(tmp_path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("user,foo\nu1,1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_public_keystroke_csv(csv_path)


def test_loader_rejects_nonnumeric_timestamps(tmp_path):
    csv_path = tmp_path / "bad2.csv"
    write_fixture_csv(csv_path, [("u1", "not_a_number", 80.0, "h")])
    with pytest.raises(ValueError):
        load_public_keystroke_csv(csv_path)


def test_loader_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_public_keystroke_csv("does_not_exist.csv")


def test_loader_rejects_release_before_press(tmp_path):
    # A KEY_UP timestamped before its own KEY_DOWN is a corrupted/misaligned
    # row (a sign of a bad export, misaligned columns, etc.) -- the module's
    # own docstring says malformed data "must fail loudly, not produce a
    # partial, silently-wrong corpus." Silently clamping this into a
    # zero-dwell keystroke would quietly bias dwell_mean/dwell_std toward
    # zero instead of surfacing the bad row.
    csv_path = tmp_path / "bad3.csv"
    write_fixture_csv(csv_path, [("u1", 100.0, 40.0, "h")])
    with pytest.raises(ValueError):
        load_public_keystroke_csv(csv_path)


def test_loaded_stream_feeds_windowing_as_keyboard_only(ml_config, tmp_path):
    csv_path = tmp_path / "fixture.csv"
    rows = []
    t = 0.0
    words = "the quick brown fox jumps over the lazy dog " * 5
    for ch in words:
        rows.append(("u1", t, t + 70.0, ch))
        t += 180.0
    write_fixture_csv(csv_path, rows)

    per_user = load_public_keystroke_csv(csv_path)
    windows = extract_windows(
        per_user["u1"],
        [],
        [],
        user_id="u1",
        session_id="public-s1",
        segment_id="public-seg1",
        collection_day="2026-01-01",
        provenance=Provenance.PUBLIC,
        config=ml_config,
    )
    assert windows
    assert all(w.provenance == Provenance.PUBLIC for w in windows)
    # PLAN.md Section 9.1: public datasets carry no mouse data -- structurally
    # verify no window from this source ever gets a mouse feature block.
    assert all(w.mouse_features is None for w in windows)
    # No mouse events exist in this source at all, so FULL is impossible;
    # a dense-enough keyboard stream should still clear the keyboard gate.
    assert any(w.quality_label == QualityLabel.KBD_ONLY for w in windows)
    assert all(w.quality_label != QualityLabel.FULL for w in windows)
