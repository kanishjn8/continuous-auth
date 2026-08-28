from __future__ import annotations

from pathlib import Path

from ml.features.schema import DeviceClass, KeyboardEvent, KeyClass
from tools.faultinjection import FaultCase, load_fault_matrix
from tools.faultinjection.frames import duplicate_frame, malformed_frame, out_of_order_frame


def _event(seq: int) -> KeyboardEvent:
    return KeyboardEvent(
        type="KEY_DOWN",
        t_capture_us=seq + 1,
        key_class=KeyClass.OTHER,
        is_repeat=False,
        device_class=DeviceClass.UNKNOWN,
        app_id=0,
        seq=seq,
    )


def test_fault_matrix_contains_every_plan_scenario_once() -> None:
    values = load_fault_matrix(Path("config/faultinjection.development.yaml"))
    assert len(values) == len(FaultCase)
    assert set(values) == set(FaultCase)


def test_transport_faults_are_length_framed_and_content_free() -> None:
    malformed = malformed_frame()
    assert int.from_bytes(malformed[:4], "big") == 1
    duplicate = duplicate_frame(_event(1))
    assert duplicate.count(b'"seq":1') == 2
    reversed_frames = out_of_order_frame(_event(1), _event(2))
    assert reversed_frames.find(b'"seq":2') < reversed_frames.find(b'"seq":1')
