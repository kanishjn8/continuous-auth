"""Content-free C1 transport faults for the live ingestion boundary."""

from __future__ import annotations

import json

from protocol.generated.python.contracts import EventFrame


def _frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, byteorder="big") + payload


def malformed_frame() -> bytes:
    return _frame(b"{")


def truncated_frame(event: EventFrame) -> bytes:
    payload = event.model_dump_json().encode()
    framed = _frame(payload)
    return framed[:-1]


def duplicate_frame(event: EventFrame) -> bytes:
    payload = event.model_dump_json().encode()
    return _frame(payload) + _frame(payload)


def out_of_order_frame(first: EventFrame, second: EventFrame) -> bytes:
    if first.seq >= second.seq:
        raise ValueError("fault input must begin in increasing sequence order")
    values = (second, first)
    return b"".join(_frame(value.model_dump_json().encode()) for value in values)


def write_pipe_fault(pipe_name: str, payload: bytes) -> None:
    """Write bytes to a running Windows backend pipe without retaining event data."""

    if not pipe_name or "\\" in pipe_name or "/" in pipe_name:
        raise ValueError("pipe name must be a non-path identifier")
    endpoint = rf"\\.\pipe\{pipe_name}"
    with open(endpoint, "wb", buffering=0) as stream:
        stream.write(payload)


def describe_frame(payload: bytes) -> str:
    """Return safe structural diagnostics only, never decoded event fields."""

    return json.dumps({"byte_count": len(payload), "sha256_required": True})
