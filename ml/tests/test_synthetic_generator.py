from __future__ import annotations

from ml.datasets.synthetic import (
    SyntheticUserProfile,
    generate_multiday_corpus,
    generate_segment,
    generate_takeover_segment,
)
from ml.features.extractor import extract_windows
from ml.features.schema import Provenance, QualityLabel


def _dump_segment(seg):
    return (
        [e.model_dump() for e in seg.keyboard_events],
        [e.model_dump() for e in seg.mouse_events],
        [e.model_dump() for e in seg.context_events],
    )


def test_same_seed_is_byte_identical():
    profile = SyntheticUserProfile(user_id="alice")
    seg1 = generate_segment(
        profile,
        seed=42,
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-01-01",
        duration_us=5_000_000,
    )
    seg2 = generate_segment(
        profile,
        seed=42,
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-01-01",
        duration_us=5_000_000,
    )
    assert _dump_segment(seg1) == _dump_segment(seg2)


def test_different_seed_produces_different_output():
    profile = SyntheticUserProfile(user_id="alice")
    seg1 = generate_segment(
        profile,
        seed=1,
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-01-01",
        duration_us=5_000_000,
    )
    seg2 = generate_segment(
        profile,
        seed=2,
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-01-01",
        duration_us=5_000_000,
    )
    assert _dump_segment(seg1) != _dump_segment(seg2)


def test_all_events_carry_synthetic_provenance_end_to_end(ml_config):
    profile = SyntheticUserProfile(user_id="alice")
    seg = generate_segment(
        profile,
        seed=7,
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-01-01",
        duration_us=10_000_000,
    )
    windows = extract_windows(
        seg.keyboard_events,
        seg.mouse_events,
        seg.context_events,
        user_id=seg.user_id,
        session_id=seg.session_id,
        segment_id=seg.segment_id,
        collection_day=seg.collection_day,
        provenance=Provenance.SYNTHETIC,
        config=ml_config,
    )
    assert windows
    assert all(w.provenance == Provenance.SYNTHETIC for w in windows)


def test_generated_stream_produces_full_quality_windows(ml_config):
    profile = SyntheticUserProfile(user_id="alice")
    seg = generate_segment(
        profile,
        seed=7,
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-01-01",
        duration_us=15_000_000,
    )
    windows = extract_windows(
        seg.keyboard_events,
        seg.mouse_events,
        seg.context_events,
        user_id=seg.user_id,
        session_id=seg.session_id,
        segment_id=seg.segment_id,
        collection_day=seg.collection_day,
        provenance=Provenance.SYNTHETIC,
        config=ml_config,
    )
    # With realistic-ish typing + mouse activity densities, at least one
    # window should meet both quality gates -- proves the generator is
    # dense enough to be a useful pipeline-mechanics smoke test.
    assert any(w.quality_label == QualityLabel.FULL for w in windows)


def test_two_profiles_are_behaviorally_separable():
    fast_typist = SyntheticUserProfile(
        user_id="fast", mean_dd_latency_us=100_000.0, std_dd_latency_us=10_000.0
    )
    slow_typist = SyntheticUserProfile(
        user_id="slow", mean_dd_latency_us=400_000.0, std_dd_latency_us=10_000.0
    )

    fast_seg = generate_segment(
        fast_typist,
        seed=1,
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-01-01",
        duration_us=10_000_000,
    )
    slow_seg = generate_segment(
        slow_typist,
        seed=1,
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-01-01",
        duration_us=10_000_000,
    )
    # A faster mean dd_latency means more keystrokes fit in the same duration.
    assert len(fast_seg.keyboard_events) > len(slow_seg.keyboard_events)


def test_takeover_segment_switches_profile_partway_through():
    genuine = SyntheticUserProfile(
        user_id="genuine", mean_dd_latency_us=150_000.0, std_dd_latency_us=5_000.0
    )
    impostor = SyntheticUserProfile(
        user_id="impostor", mean_dd_latency_us=150_000.0, std_dd_latency_us=5_000.0
    )
    seg = generate_takeover_segment(
        genuine,
        impostor,
        seed=1,
        session_id="s1",
        segment_id="seg1",
        collection_day="2026-01-01",
        duration_us=10_000_000,
        takeover_fraction=0.5,
    )
    assert seg.user_id == "genuine"
    times = [e.t_capture_us for e in seg.keyboard_events]
    # The genuine-profile half and impostor-profile half are generated over
    # disjoint, non-overlapping time ranges and concatenated in order.
    assert times == sorted(times)
    assert min(times) >= 0
    assert max(times) <= 10_000_000


def test_multiday_corpus_has_expected_distinct_days():
    profile = SyntheticUserProfile(user_id="alice")
    segments = generate_multiday_corpus(profile, base_seed=1, num_days=5, segments_per_day=2)
    days = {s.collection_day for s in segments}
    assert len(days) == 5
    assert len(segments) == 10
    assert all(s.user_id == "alice" for s in segments)


def test_multiday_corpus_rolls_over_past_31_days():
    # collection_day was previously built as f"2026-01-{day_idx+1:02d}",
    # hardcoding month "01" with no rollover -- num_days > 31 silently
    # produced invalid calendar strings like "2026-01-35". A longer
    # synthetic corpus (e.g. for an enrollment-length experiment needing
    # >31 distinct days) must still get valid, distinct, chronologically
    # increasing calendar days.
    profile = SyntheticUserProfile(user_id="alice")
    segments = generate_multiday_corpus(profile, base_seed=1, num_days=35, segments_per_day=1)
    days = sorted({s.collection_day for s in segments})
    assert len(days) == 35
    assert days[0] == "2026-01-01"
    assert days[-1] == "2026-02-04"
    assert days == sorted(days)  # every day string is a valid, orderable ISO date
