from __future__ import annotations

import json
import struct

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError

from ml.features.config import load_config as load_ml_config
from protocol.generated.python.contracts import DataProvenance, EventFrame, WindowQuality
from tools.synthetic.config import SyntheticConfig, SyntheticConfigError, load_synthetic_config
from tools.synthetic.framing import FRAME_HEADER, SyntheticFrameError, canonical_payload
from tools.synthetic.generator import generate_scenario
from tools.synthetic.output import write_bundle


@pytest.fixture(scope="module")
def synthetic_config():
    return load_synthetic_config()


@pytest.fixture(scope="module")
def ml_config():
    return load_ml_config()


def _logical_bytes(bundle) -> bytes:
    return b"\n".join(canonical_payload(event) for event in bundle.events)


def _frame_payloads(stream: bytes) -> list[bytes]:
    payloads: list[bytes] = []
    offset = 0
    while offset < len(stream):
        (size,) = FRAME_HEADER.unpack_from(stream, offset)
        offset += FRAME_HEADER.size
        payloads.append(stream[offset : offset + size])
        offset += size
    assert offset == len(stream)
    return payloads


def test_same_config_and_seed_are_byte_identical(synthetic_config, ml_config):
    first = generate_scenario(synthetic_config, "baseline", ml_config=ml_config)
    second = generate_scenario(synthetic_config, "baseline", ml_config=ml_config)
    assert _logical_bytes(first) == _logical_bytes(second)
    assert first.framed_streams == second.framed_streams
    assert first.expectations == second.expectations


def test_every_event_is_c1_valid_ordered_and_content_free(synthetic_config, ml_config):
    bundle = generate_scenario(synthetic_config, "baseline", ml_config=ml_config)
    adapter = TypeAdapter(EventFrame)
    forbidden = {
        "keycode",
        "typed_text",
        "window_title",
        "document_name",
        "file_path",
        "url",
        "clipboard",
        "screenshot",
    }
    for expected_seq, event in enumerate(bundle.events):
        document = event.model_dump(mode="python")
        adapter.validate_python(document)
        assert expected_seq == event.seq
        assert forbidden.isdisjoint(document)
    timestamps = [event.t_capture_us for event in bundle.events]
    assert timestamps == sorted(timestamps)


def test_known_intervals_exactly_reconstruct_capture_timing(synthetic_config, ml_config):
    bundle = generate_scenario(synthetic_config, "baseline", ml_config=ml_config)
    reconstructed = tuple(
        current.t_capture_us - previous.t_capture_us
        for previous, current in zip(bundle.events, bundle.events[1:], strict=False)
    )
    assert reconstructed == bundle.expectations.event_intervals_us
    assert all(interval >= 0 for interval in reconstructed)


def test_outputs_are_synthetic_and_excluded_from_headline_results(synthetic_config, ml_config):
    bundle = generate_scenario(synthetic_config, "baseline", ml_config=ml_config)
    assert bundle.expectations.provenance == DataProvenance.SYNTHETIC
    assert bundle.expectations.headline_evaluation_allowed is False
    assert bundle.feature_windows
    assert all(window.provenance == DataProvenance.SYNTHETIC for window in bundle.feature_windows)


def test_sparse_and_takeover_scenarios_cover_downstream_assertions(synthetic_config, ml_config):
    sparse = generate_scenario(synthetic_config, "keyboard_sparse", ml_config=ml_config)
    assert sparse.feature_windows
    assert all(
        window.quality_label in (WindowQuality.KBD_ONLY, WindowQuality.INSUFFICIENT_DATA)
        for window in sparse.feature_windows
    )
    assert all(not score.mouse.available for score in sparse.scores)

    takeover = generate_scenario(synthetic_config, "takeover", ml_config=ml_config)
    assert takeover.expectations.takeover_at_us is not None
    assert {item.phase for item in takeover.expectations.windows} == {"genuine", "takeover"}
    by_window = {score.window_id: score for score in takeover.scores}
    for expected in takeover.expectations.windows:
        score = by_window[expected.window_id]
        if expected.phase == "takeover":
            assert score.keyboard.calibrated_score == pytest.approx(0.9)
            assert expected.expected_risk_level.value == "HIGH"


def test_fault_streams_are_isolated_and_deterministic(synthetic_config, ml_config):
    bundle = generate_scenario(synthetic_config, "baseline", ml_config=ml_config)
    streams = bundle.framed_streams
    normal = _frame_payloads(streams.normal)
    gap = _frame_payloads(streams.gap)
    duplicate = _frame_payloads(streams.duplicate)
    out_of_order = _frame_payloads(streams.out_of_order)
    malformed = _frame_payloads(streams.malformed)

    assert len(gap) == len(normal) - 1
    assert len(duplicate) == len(normal) + 1
    assert len(out_of_order) == len(normal)
    assert malformed[:-1] == normal
    with pytest.raises(json.JSONDecodeError):
        json.loads(malformed[-1])
    (declared_oversize,) = struct.unpack(">I", streams.oversized)
    assert declared_oversize == synthetic_config.max_frame_bytes + 1


def test_writer_validates_then_creates_complete_fixture_set(synthetic_config, ml_config, tmp_path):
    bundle = generate_scenario(synthetic_config, "baseline", ml_config=ml_config)
    destination = tmp_path / "baseline"
    write_bundle(bundle, destination)
    expected = json.loads((destination / "expected.json").read_text(encoding="utf-8"))
    assert expected["provenance"] == "SYNTHETIC"
    assert expected["headline_evaluation_allowed"] is False
    assert (destination / "events.jsonl").is_file()
    assert (destination / "feature_windows.jsonl").is_file()
    assert (destination / "scores.jsonl").is_file()
    assert (destination / "frames.bin").read_bytes() == bundle.framed_streams.normal
    assert len(list((destination / "faults").glob("*.bin"))) == 5


def test_invalid_configuration_is_rejected_without_clipping(synthetic_config, tmp_path):
    document = synthetic_config.model_dump(mode="json")
    document["profiles"]["genuine"]["mouse_click_probability"] = 0.8
    document["profiles"]["genuine"]["mouse_scroll_probability"] = 0.8
    config_path = tmp_path / "invalid.yaml"
    config_path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(SyntheticConfigError, match="probabilities"):
        load_synthetic_config(config_path)


def test_unknown_fields_and_bad_fault_indices_fail_loudly(synthetic_config, ml_config):
    document = synthetic_config.model_dump(mode="python")
    document["unexpected"] = True
    with pytest.raises(ValidationError):
        SyntheticConfig.model_validate(document)

    changed = synthetic_config.model_dump(mode="python")
    changed["scenarios"]["baseline"]["faults"]["gap_event_index"] = 10**9
    bad_config = SyntheticConfig.model_validate(changed)
    with pytest.raises(SyntheticFrameError, match="outside"):
        generate_scenario(bad_config, "baseline", ml_config=ml_config)


@settings(max_examples=12, deadline=None)
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_seeded_scenarios_preserve_invariants(synthetic_config, ml_config, seed):
    document = synthetic_config.model_dump(mode="python")
    document["scenarios"]["baseline"]["seed"] = seed
    config = SyntheticConfig.model_validate(document)
    bundle = generate_scenario(config, "baseline", ml_config=ml_config)
    assert bundle.events
    assert [event.seq for event in bundle.events] == list(range(len(bundle.events)))
    assert all(
        event.t_capture_us >= config.scenarios["baseline"].t_start_us for event in bundle.events
    )
