from __future__ import annotations

import json
import struct
from dataclasses import replace
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Any

import pytest

from backend.app.ingestion import (
    AttributedEvent,
    AuthenticatedEntry,
    AvailabilityEvent,
    IngestionConfigError,
    IngestionLifecycleError,
    IngestionPipeline,
    IngestionSettings,
    LifecycleEvent,
    load_ingestion_settings,
)
from backend.app.ingestion.framing import FRAME_HEADER, IncrementalFrameDecoder
from backend.app.ingestion.ordering import SequenceTracker
from backend.app.ingestion.validation import validate_payload
from ml.features.config import load_config as load_ml_config
from protocol.generated.python.contracts import PROTOCOL_VERSION
from tools.synthetic import generate_scenario, load_synthetic_config
from tools.synthetic.framing import canonical_payload, frame_payload
from tools.synthetic.generator import ScenarioBundle


def _settings(**changes: Any) -> IngestionSettings:
    base = IngestionSettings(
        config_version="test-v1",
        protocol_version=PROTOCOL_VERSION,
        max_frame_bytes=65536,
        raw_event_ring_capacity=32,
        idle_split_seconds=900,
    )
    return replace(base, **changes)


def _entry(session_id: str = "session-test") -> AuthenticatedEntry:
    return AuthenticatedEntry(
        user_id="synthetic-user",
        evidence_id="synthetic-login-proof",
        authenticated=True,
        wall_clock_anchor=datetime(2026, 1, 1, tzinfo=UTC),
        session_id=session_id,
    )


def _ids(prefix: str) -> str:
    return f"{prefix}-{next(_ID_SEQUENCE)}"


_ID_SEQUENCE = count(1)


@pytest.fixture(scope="module")
def baseline_bundle() -> ScenarioBundle:
    return generate_scenario(
        load_synthetic_config(),
        "baseline",
        ml_config=load_ml_config(),
    )


def test_development_config_loads_and_missing_or_extra_values_fail(tmp_path: Path) -> None:
    settings = load_ingestion_settings()
    assert settings.idle_split_us == 900_000_000
    assert settings.max_frame_bytes == 65536

    missing = tmp_path / "missing.yaml"
    with pytest.raises(IngestionConfigError, match="cannot read"):
        load_ingestion_settings(missing)

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(
        "config_version: x\nprotocol_version: 1.0.0\ningestion:\n"
        "  max_frame_bytes: 0\n  raw_event_ring_capacity: 1\n"
        "  idle_split_seconds: 1\n  unexpected: true\n",
        encoding="utf-8",
    )
    with pytest.raises(IngestionConfigError, match="failed validation"):
        load_ingestion_settings(invalid)


def test_incremental_decoder_accepts_every_chunk_boundary(
    baseline_bundle: ScenarioBundle,
) -> None:
    stream = baseline_bundle.framed_streams.normal
    expected = [canonical_payload(event) for event in baseline_bundle.events]
    for split in range(min(len(stream), 256)):
        decoder = IncrementalFrameDecoder(65536)
        payloads = [
            *decoder.feed(stream[:split]).payloads,
            *decoder.feed(stream[split:]).payloads,
        ]
        assert payloads == expected
        assert decoder.finish().issues == ()


def test_decoder_rejects_zero_oversized_and_truncated_then_recovers() -> None:
    decoder = IncrementalFrameDecoder(16)
    zero = decoder.feed(FRAME_HEADER.pack(0))
    assert [issue.code for issue in zero.issues] == ["ZERO_LENGTH_FRAME"]
    oversized = decoder.feed(FRAME_HEADER.pack(17))
    assert [issue.code for issue in oversized.issues] == ["OVERSIZED_FRAME"]
    assert decoder.feed(FRAME_HEADER.pack(5) + b"ab").payloads == ()
    assert decoder.finish().issues[0].code == "TRUNCATED_FRAME"
    assert decoder.feed(FRAME_HEADER.pack(2) + b"{}").payloads == (b"{}",)


def test_validation_rejects_malformed_unknown_version_and_extra_fields(
    baseline_bundle: ScenarioBundle,
) -> None:
    malformed = validate_payload(b"{").issue
    assert malformed is not None and malformed.code == "MALFORMED_JSON"
    document = baseline_bundle.events[0].model_dump(mode="json")
    document["schema_version"] = "9.9.9"
    bad_version = validate_payload(json.dumps(document).encode()).issue
    assert bad_version is not None and bad_version.code == "INVALID_EVENT_SCHEMA"
    document["schema_version"] = PROTOCOL_VERSION
    document["typed_content"] = "forbidden"
    extra_field = validate_payload(json.dumps(document).encode()).issue
    assert extra_field is not None and extra_field.code == "INVALID_EVENT_SCHEMA"
    assert "forbidden" not in extra_field.detail


def test_sequence_tracker_gap_duplicate_late_and_no_wrap_policy() -> None:
    tracker = SequenceTracker(history_capacity=3)
    assert tracker.observe(10).status == "ACCEPTED"
    assert tracker.observe(12).missing_sequences == 1
    assert tracker.observe(12).status == "DUPLICATE"
    assert tracker.observe(9).status == "OUT_OF_ORDER"
    assert tracker.observe(13).status == "ACCEPTED"

    near_boundary = SequenceTracker(history_capacity=2)
    assert near_boundary.observe(2**63 - 2).accepted
    assert near_boundary.observe(2**63 - 1).accepted
    assert near_boundary.observe(0).status == "OUT_OF_ORDER"

    timestamps = SequenceTracker(history_capacity=3)
    assert timestamps.observe(0, 100).accepted
    assert timestamps.observe(1, 99).status == "OUT_OF_ORDER"
    assert timestamps.observe(2, 101).accepted


def test_authenticated_session_required_and_failed_auth_does_not_activate(
    baseline_bundle: ScenarioBundle,
) -> None:
    pipeline = IngestionPipeline(_settings(), id_factory=_ids)
    no_session = pipeline.feed(baseline_bundle.framed_streams.normal)
    assert no_session.events == ()
    assert no_session.counters.inactive_session_rejections == len(baseline_bundle.events)

    rejected = pipeline.start_session(replace(_entry(), authenticated=False))
    assert rejected.kind == "SESSION_START_REJECTED"
    assert pipeline.active_session_id is None

    started = pipeline.start_session(_entry())
    assert started.kind == "SESSION_STARTED"
    replay = pipeline.feed(baseline_bundle.framed_streams.normal)
    assert replay.counters.events_accepted == len(baseline_bundle.events)
    with pytest.raises(IngestionLifecycleError, match="another is active"):
        pipeline.start_session(_entry("second"))


def test_pipeline_ingests_synthetic_stream_without_retimestamping(
    baseline_bundle: ScenarioBundle,
) -> None:
    emitted: list[AttributedEvent] = []
    lifecycle: list[LifecycleEvent] = []
    pipeline = IngestionPipeline(
        _settings(raw_event_ring_capacity=1024),
        event_sink=emitted.append,
        lifecycle_sink=lifecycle.append,
        id_factory=_ids,
    )
    pipeline.start_session(_entry())
    stream = baseline_bundle.framed_streams.normal
    accepted: list[AttributedEvent] = []
    for offset in range(0, len(stream), 7):
        accepted.extend(pipeline.feed(stream[offset : offset + 7]).events)

    assert len(accepted) == len(baseline_bundle.events)
    assert emitted == accepted
    assert [item.event.t_capture_us for item in accepted] == [
        item.t_capture_us for item in baseline_bundle.events
    ]
    assert all(item.session_id == "session-test" for item in accepted)
    assert len({item.segment_id for item in accepted}) == 1
    assert [item.event.seq for item in accepted] == list(range(len(accepted)))
    assert any(item.kind == "SEGMENT_STARTED" for item in lifecycle)


def test_fault_streams_are_counted_and_do_not_crash(
    baseline_bundle: ScenarioBundle,
) -> None:
    scenarios = {
        "gap": ("missing_sequences", 1),
        "duplicate": ("duplicate_rejections", 1),
        "out_of_order": ("out_of_order_rejections", 1),
        "malformed": ("malformed_frames", 1),
        "oversized": ("oversized_frames", 1),
    }
    for stream_name, (counter_name, minimum) in scenarios.items():
        pipeline = IngestionPipeline(_settings(raw_event_ring_capacity=1024), id_factory=_ids)
        pipeline.start_session(_entry(f"session-{stream_name}"))
        stream = getattr(baseline_bundle.framed_streams, stream_name)
        pipeline.feed(stream)
        assert getattr(pipeline.counters, counter_name) >= minimum


def test_idle_boundary_splits_only_when_threshold_is_exceeded(
    baseline_bundle: ScenarioBundle,
) -> None:
    source = baseline_bundle.events[0]
    documents = []
    for sequence, timestamp in enumerate((1_000_000, 6_000_000, 11_000_001)):
        document = source.model_dump(mode="python")
        document["seq"] = sequence
        document["t_capture_us"] = timestamp
        documents.append(type(source).model_validate(document))
    stream = b"".join(
        frame_payload(canonical_payload(event), max_frame_bytes=65536) for event in documents
    )
    pipeline = IngestionPipeline(
        _settings(idle_split_seconds=5),
        id_factory=_ids,
    )
    pipeline.start_session(_entry())
    batch = pipeline.feed(stream)
    segment_ids = [item.segment_id for item in batch.events]
    assert segment_ids[0] == segment_ids[1]
    assert segment_ids[2] != segment_ids[1]
    assert [event.kind for event in batch.lifecycle].count("SEGMENT_STARTED") == 2
    assert [event.kind for event in batch.lifecycle].count("SEGMENT_ENDED") == 1


def test_wall_clock_anchor_never_changes_capture_timing(
    baseline_bundle: ScenarioBundle,
) -> None:
    anchors = (
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2035, 8, 1, tzinfo=UTC),
    )
    observed = []
    single_frame = frame_payload(
        canonical_payload(baseline_bundle.events[0]),
        max_frame_bytes=65536,
    )
    for index, anchor in enumerate(anchors):
        pipeline = IngestionPipeline(_settings(), id_factory=_ids)
        pipeline.start_session(replace(_entry(f"session-{index}"), wall_clock_anchor=anchor))
        observed.append(pipeline.feed(single_frame).events[0].event.t_capture_us)
    assert observed == [baseline_bundle.events[0].t_capture_us] * 2


def test_raw_event_ring_is_memory_only_and_bounded(
    baseline_bundle: ScenarioBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    pipeline = IngestionPipeline(_settings(raw_event_ring_capacity=5), id_factory=_ids)
    pipeline.start_session(_entry())
    pipeline.feed(baseline_bundle.framed_streams.normal)
    assert len(pipeline.raw_event_ring) == 5
    assert pipeline.counters.raw_ring_overwrites == len(baseline_bundle.events) - 5
    assert list(tmp_path.iterdir()) == []


def test_shutdown_partial_frame_and_recovery_preserve_session_and_sequence(
    baseline_bundle: ScenarioBundle,
) -> None:
    events = baseline_bundle.events[:2]
    frames = [frame_payload(canonical_payload(event), max_frame_bytes=65536) for event in events]
    pipeline = IngestionPipeline(_settings(), id_factory=_ids)
    pipeline.start_session(_entry())
    first = pipeline.feed(frames[0])
    assert len(first.events) == 1
    pipeline.feed(frames[1][:8])
    recovery = pipeline.recover_transport()
    assert recovery.counters.truncated_frames == 1
    second = pipeline.feed(frames[1])
    assert len(second.events) == 1
    ended = pipeline.shutdown()
    assert ended[-1].kind == "SESSION_ENDED"
    assert ended[-1].reason == "SHUTDOWN"


def test_infrastructure_failure_is_loud_but_session_remains_active() -> None:
    availability: list[AvailabilityEvent] = []
    pipeline = IngestionPipeline(_settings(), availability_sink=availability.append)
    pipeline.start_session(_entry())
    event = pipeline.report_infrastructure_failure(
        code="MODEL_UNAVAILABLE",
        component="model",
        detail="synthetic failure",
    )
    assert event in availability
    assert pipeline.counters.availability_events == 1
    assert pipeline.active_session_id == "session-test"


def test_protocol_example_acts_as_cross_language_golden_frame() -> None:
    fixture = Path("protocol/examples/valid/keyboard_event.json").read_bytes()
    payload = json.loads(fixture)
    payload["seq"] = 0
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    framed = struct.pack(">I", len(encoded)) + encoded
    pipeline = IngestionPipeline(_settings())
    pipeline.start_session(_entry())
    batch = pipeline.feed(framed)
    assert len(batch.events) == 1
    assert batch.events[0].event.model_dump(mode="json") == payload
