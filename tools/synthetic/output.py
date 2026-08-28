"""Validated, atomic output writer for generated synthetic fixtures."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path

from pydantic import TypeAdapter

from protocol.generated.python.contracts import EventFrame, FeatureWindow, ScoreResult
from tools.synthetic.framing import canonical_payload
from tools.synthetic.generator import ScenarioBundle

EVENT_ADAPTER: TypeAdapter[EventFrame] = TypeAdapter(EventFrame)
FEATURE_ADAPTER: TypeAdapter[FeatureWindow] = TypeAdapter(FeatureWindow)
SCORE_ADAPTER: TypeAdapter[ScoreResult] = TypeAdapter(ScoreResult)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary_name)
        raise


def write_bundle(bundle: ScenarioBundle, destination: Path | str) -> None:
    """Validate every contract before creating any destination files."""

    for event in bundle.events:
        EVENT_ADAPTER.validate_python(event.model_dump(mode="python"))
    for window in bundle.feature_windows:
        FEATURE_ADAPTER.validate_python(window.model_dump(mode="python"))
    for score in bundle.scores:
        SCORE_ADAPTER.validate_python(score.model_dump(mode="python"))

    event_bytes = b"\n".join(canonical_payload(event) for event in bundle.events) + b"\n"
    feature_bytes = (
        b"\n".join(
            json.dumps(
                window.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            for window in bundle.feature_windows
        )
        + b"\n"
    )
    score_bytes = (
        b"\n".join(
            json.dumps(score.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
            for score in bundle.scores
        )
        + b"\n"
    )
    expected_bytes = json.dumps(
        asdict(bundle.expectations),
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: value.value,
    ).encode("utf-8")

    root = Path(destination)
    outputs = {
        root / "events.jsonl": event_bytes,
        root / "feature_windows.jsonl": feature_bytes,
        root / "scores.jsonl": score_bytes,
        root / "expected.json": expected_bytes,
        root / "frames.bin": bundle.framed_streams.normal,
        root / "faults" / "gap.bin": bundle.framed_streams.gap,
        root / "faults" / "duplicate.bin": bundle.framed_streams.duplicate,
        root / "faults" / "out_of_order.bin": bundle.framed_streams.out_of_order,
        root / "faults" / "malformed.bin": bundle.framed_streams.malformed,
        root / "faults" / "oversized.bin": bundle.framed_streams.oversized,
    }
    for path, payload in outputs.items():
        _atomic_write(path, payload)
