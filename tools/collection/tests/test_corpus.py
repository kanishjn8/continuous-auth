"""A frozen corpus is loaded only through its verified manifest."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.storage.database import SQLiteDatabase
from ml.features.schema import FeatureWindow
from protocol.generated.python.contracts import DataProvenance
from tools.collection.config import CollectionSettings
from tools.collection.corpus import load_frozen_corpus
from tools.collection.eligibility import ConsentRecord, EnrollmentRecord
from tools.collection.freeze import FreezeError, build_freeze
from tools.collection.repository import load_window_summaries

_CONTEXT = {
    "dominant_category": "UNKNOWN",
    "category_fractions": {"UNKNOWN": 1.0},
    "app_switch_rate": 0.0,
    "device_class": "UNKNOWN",
    "app_shares": [{"app_id": 0, "category": "UNKNOWN", "fraction": 1.0}],
}


def _settings() -> CollectionSettings:
    return CollectionSettings(
        config_version="corpus-test-1",
        target_collection_days=3,
        min_windows_per_day=1,
        min_full_modality_fraction=0.0,
        max_observed_gap_hours=999.0,
        scheduled_anchor_interval_hours=4.0,
        eligible_provenance=frozenset({DataProvenance.PILOT}),
        min_distinct_days=3,
        training_fraction=1 / 3,
        validation_fraction=1 / 3,
        evaluation_fraction=1 / 3,
    )


def _feature_window(
    *, user_id: str, window_id: str, day: str, session_id: str, segment_id: str
) -> FeatureWindow:
    return FeatureWindow.model_validate(
        {
            "schema_version": "1.0.0",
            "user_id": user_id,
            "session_id": session_id,
            "segment_id": segment_id,
            "window_id": window_id,
            "t_start_us": 100,
            "t_end_us": 200,
            "quality_label": "INSUFFICIENT_DATA",
            "key_event_count": 0,
            "mouse_event_count": 0,
            "collection_day": day,
            "provenance": "PILOT",
            "keyboard_features": None,
            "mouse_features": None,
            "context": _CONTEXT,
        }
    )


def _insert_participant(
    connection: sqlite3.Connection, *, user_id: str, session_id: str, segment_id: str
) -> None:
    connection.execute(
        "INSERT INTO users(user_id, state, schema_version, created_at_utc, updated_at_utc) "
        "VALUES (?, 'ACTIVE', '1.0.0', ?, ?)",
        (user_id, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO sessions(session_id, user_id, entry_auth_evidence, started_at_utc, "
        "schema_version) VALUES (?, ?, 'A1_LOGIN_UNLOCK', '2026-01-01T00:00:00Z', '1.0.0')",
        (session_id, user_id),
    )
    connection.execute(
        "INSERT INTO segments(segment_id, session_id, started_at_capture_us, boundary_reason, "
        "schema_version) VALUES (?, ?, 0, 'SYNTHETIC_START', '1.0.0')",
        (segment_id, session_id),
    )


def _insert_window(
    connection: sqlite3.Connection, window: FeatureWindow, *, stored_at_utc: str
) -> None:
    connection.execute(
        """
        INSERT INTO feature_windows(
            window_id, user_id, session_id, segment_id, t_start_us, t_end_us,
            quality_label, key_event_count, mouse_event_count, collection_day,
            provenance, keyboard_features_json, mouse_features_json, context_json,
            schema_version, stored_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            window.window_id,
            window.user_id,
            window.session_id,
            window.segment_id,
            window.t_start_us,
            window.t_end_us,
            window.quality_label.value,
            window.key_event_count,
            window.mouse_event_count,
            window.collection_day,
            window.provenance.value,
            (
                None
                if window.keyboard_features is None
                else json.dumps(window.keyboard_features.model_dump(mode="json"))
            ),
            (
                None
                if window.mouse_features is None
                else json.dumps(window.mouse_features.model_dump(mode="json"))
            ),
            json.dumps(window.context.model_dump(mode="json")),
            window.schema_version,
            stored_at_utc,
        ),
    )


@pytest.fixture
def frozen_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """A genuinely frozen, checksum-verified synthetic corpus.

    Windows are written into a real project-migrated SQLite database, read
    back through the project's own ``load_window_summaries``, and frozen with
    the real ``build_freeze`` -- so the manifest and database agree exactly
    the way a real pilot freeze would.
    """

    database = tmp_path / "corpus.db"
    manifest = tmp_path / "manifest.json"
    user_id = "participant_001"
    session_id = "session-001"
    segment_id = "segment-001"
    days = ["2026-02-01", "2026-02-02", "2026-02-03"]

    db = SQLiteDatabase(database, busy_timeout_ms=5000)
    db.initialise()
    with db.transaction() as connection:
        _insert_participant(
            connection, user_id=user_id, session_id=session_id, segment_id=segment_id
        )
        for index, day in enumerate(days):
            window = _feature_window(
                user_id=user_id,
                window_id=f"window-{index}",
                day=day,
                session_id=session_id,
                segment_id=segment_id,
            )
            _insert_window(connection, window, stored_at_utc=f"{day}T00:00:00Z")

    windows = load_window_summaries(database)
    consent = ConsentRecord(user_id, "approved-v1", datetime(2026, 1, 1, tzinfo=UTC))
    enrollment = EnrollmentRecord(
        user_id, datetime(2026, 1, 1, tzinfo=UTC), "collector-v1", "1.0.0"
    )
    build_freeze(
        windows,
        destination=manifest,
        version="corpus-test-v1",
        consents={user_id: consent},
        enrollments={user_id: enrollment},
        settings=_settings(),
        frozen_at=datetime(2026, 2, 10, tzinfo=UTC),
    )
    return database, manifest


@pytest.fixture
def add_window():
    """Insert a self-contained, post-freeze window under a brand-new participant."""

    def _add(database: Path, *, window_id: str) -> None:
        user_id = f"{window_id}-user"
        session_id = f"{window_id}-session"
        segment_id = f"{window_id}-segment"
        db = SQLiteDatabase(database, busy_timeout_ms=5000)
        with db.transaction() as connection:
            _insert_participant(
                connection, user_id=user_id, session_id=session_id, segment_id=segment_id
            )
            window = _feature_window(
                user_id=user_id,
                window_id=window_id,
                day="2026-02-05",
                session_id=session_id,
                segment_id=segment_id,
            )
            _insert_window(connection, window, stored_at_utc="2026-02-05T00:00:00Z")

    return _add


def test_train_partition_excludes_other_partitions(frozen_fixture) -> None:
    database, manifest = frozen_fixture
    corpus = load_frozen_corpus(database, manifest, "TRAIN")
    for user_id, windows in corpus.windows_by_user.items():
        days = corpus.day_assignments[user_id]
        assert {days[w.collection_day] for w in windows} == {"TRAIN"}


def test_a_tampered_manifest_is_refused(frozen_fixture) -> None:
    database, manifest = frozen_fixture
    text = manifest.read_text(encoding="utf-8").replace('"TRAIN"', '"EVALUATION"', 1)
    manifest.chmod(0o600)
    manifest.write_text(text, encoding="utf-8")
    with pytest.raises(FreezeError):
        load_frozen_corpus(database, manifest, "TRAIN")


def test_windows_added_after_the_freeze_are_excluded(frozen_fixture, add_window) -> None:
    """Post-freeze windows must never leak into a frozen result.

    ``verify_freeze`` raises only when a *frozen* window's checksum is missing
    or has changed; a purely added window changes no frozen record, so it
    does not trip ``verify_freeze`` (confirmed by running this test). The
    hard requirement is narrower and absolute: the loader itself must never
    return a window absent from the manifest.
    """

    database, manifest = frozen_fixture
    add_window(database, window_id="late-window")

    corpus = load_frozen_corpus(database, manifest, "TRAIN")

    assert "late-window" not in corpus.manifest_window_ids
    assert "late-window" not in corpus.observed_at_by_window
    assert all(
        window.window_id != "late-window"
        for windows in corpus.windows_by_user.values()
        for window in windows
    )


def test_observation_times_are_returned_for_every_window(frozen_fixture) -> None:
    database, manifest = frozen_fixture
    corpus = load_frozen_corpus(database, manifest, "TRAIN")
    for windows in corpus.windows_by_user.values():
        for window in windows:
            assert window.window_id in corpus.observed_at_by_window


def test_unknown_partition_is_rejected(frozen_fixture) -> None:
    database, manifest = frozen_fixture
    with pytest.raises(ValueError):
        load_frozen_corpus(database, manifest, "HOLDOUT")
