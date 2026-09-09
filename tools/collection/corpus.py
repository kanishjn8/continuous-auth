"""Load a frozen participant corpus into ML feature windows.

Nothing here reads the database without first verifying the manifest. The
freeze manifest is the corpus's integrity boundary (PLAN.md Section 9.4) and,
under ADR-013, part of its admission evidence: a corpus that no longer matches
its manifest is never loaded, and a window absent from the manifest is never
returned.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ml.features.schema import FeatureWindow

from backend.app.storage.drill import DRILL_EXCLUSION_SQL, require_drill_table
from .freeze import FreezeError, verify_freeze
from .repository import load_window_summaries

_PARTITIONS = frozenset({"TRAIN", "VALIDATION", "EVALUATION"})


@dataclass(frozen=True)
class FrozenCorpus:
    windows_by_user: dict[str, list[FeatureWindow]]
    manifest_window_ids: frozenset[str]
    observed_at_by_window: dict[str, datetime]
    day_assignments: dict[str, dict[str, str]]


def _row_to_window(row: sqlite3.Row) -> FeatureWindow:
    return FeatureWindow.model_validate(
        {
            "schema_version": row["schema_version"],
            "user_id": row["user_id"],
            "session_id": row["session_id"],
            "segment_id": row["segment_id"],
            "window_id": row["window_id"],
            "t_start_us": row["t_start_us"],
            "t_end_us": row["t_end_us"],
            "quality_label": row["quality_label"],
            "key_event_count": row["key_event_count"],
            "mouse_event_count": row["mouse_event_count"],
            "collection_day": row["collection_day"],
            "provenance": row["provenance"],
            "keyboard_features": (
                None
                if row["keyboard_features_json"] is None
                else json.loads(row["keyboard_features_json"])
            ),
            "mouse_features": (
                None
                if row["mouse_features_json"] is None
                else json.loads(row["mouse_features_json"])
            ),
            "context": json.loads(row["context_json"]),
        }
    )


def load_frozen_corpus(database: Path, manifest: Path, partition: str) -> FrozenCorpus:
    """Return one partition of a checksum-verified frozen corpus.

    ``verify_freeze`` runs before any window is read, and a window present in
    the database but absent from the manifest (for example one written after
    the freeze) is silently excluded rather than returned: the manifest is
    the sole source of truth for corpus membership.
    """

    if partition not in _PARTITIONS:
        raise ValueError(f"unknown partition {partition!r}; expected one of {sorted(_PARTITIONS)}")

    # Verify before reading. A mismatch is a hard stop, never a warning.
    verify_freeze(manifest, load_window_summaries(database))

    document: Any = json.loads(manifest.read_text(encoding="utf-8"))
    records = {record["window_id"]: record for record in document["records"]}
    wanted = {
        window_id for window_id, record in records.items() if record["partition"] == partition
    }

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        require_drill_table(connection)
        rows = connection.execute(
            f"""
            SELECT window_id, user_id, session_id, segment_id, t_start_us, t_end_us,
                   quality_label, key_event_count, mouse_event_count, collection_day,
                   provenance, keyboard_features_json, mouse_features_json, context_json,
                   schema_version, stored_at_utc
            FROM feature_windows
            -- Belt and braces. A drill window cannot reach a manifest, because
            -- load_window_summaries already excluded it before build_freeze ran
            -- -- but this loader reads feature_windows directly rather than
            -- through that function, so it repeats the filter rather than
            -- inheriting it. A corpus loader must never depend on someone
            -- else having filtered first (ADR-014).
            {DRILL_EXCLUSION_SQL}
            ORDER BY user_id, window_id
            """
        ).fetchall()
    finally:
        connection.close()

    windows_by_user: dict[str, list[FeatureWindow]] = defaultdict(list)
    observed_at: dict[str, datetime] = {}
    for row in rows:
        window_id = str(row["window_id"])
        if window_id not in records:
            # Present in the database but not in the manifest: a post-freeze
            # window. It is silently excluded rather than quietly included.
            continue
        observed_at[window_id] = datetime.fromisoformat(
            str(row["stored_at_utc"]).replace("Z", "+00:00")
        ).astimezone(UTC)
        if window_id in wanted:
            windows_by_user[str(row["user_id"])].append(_row_to_window(row))

    missing = wanted - set(observed_at)
    if missing:
        raise FreezeError(f"{len(missing)} frozen windows are absent from the database")

    return FrozenCorpus(
        windows_by_user=dict(windows_by_user),
        manifest_window_ids=frozenset(records),
        observed_at_by_window=observed_at,
        day_assignments=document["day_assignments"],
    )
