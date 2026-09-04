"""First-profile activation trains on TRAIN and never reads EVALUATION."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.risk.config import load_risk_settings
from backend.app.storage.config import load_storage_settings
from backend.app.storage.database import SQLiteDatabase
from backend.app.storage.service import StorageService
from ml.features.config import load_config as load_ml_config
from ml.features.schema import Provenance
from ml.tests.conftest import generate_multiday_user_windows
from tools.collection.config import load_collection_settings

ROOT = Path(__file__).resolve().parents[3]
CONSENTED_AT = "2025-12-01T00:00:00Z"


def _insert_participant(
    connection: sqlite3.Connection, *, user_id: str, session_id: str, segment_id: str
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO users(user_id, state, schema_version, created_at_utc, "
        "updated_at_utc) VALUES (?, 'ACTIVE', '1.0.0', ?, ?)",
        (user_id, CONSENTED_AT, CONSENTED_AT),
    )
    connection.execute(
        "INSERT OR IGNORE INTO sessions(session_id, user_id, entry_auth_evidence, "
        "started_at_utc, schema_version) VALUES (?, ?, 'A1_LOGIN_UNLOCK', ?, '1.0.0')",
        (session_id, user_id, CONSENTED_AT),
    )
    connection.execute(
        "INSERT OR IGNORE INTO segments(segment_id, session_id, started_at_capture_us, "
        "boundary_reason, schema_version) VALUES (?, ?, 0, 'SYNTHETIC_START', '1.0.0')",
        (segment_id, session_id),
    )


def _insert_window(connection: sqlite3.Connection, window, *, stored_at_utc: str) -> None:
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


def _insert_corpus(database: Path, windows: list) -> None:
    db = SQLiteDatabase(database, busy_timeout_ms=5000)
    with db.transaction() as connection:
        for window in windows:
            _insert_participant(
                connection,
                user_id=window.user_id,
                session_id=window.session_id,
                segment_id=window.segment_id,
            )
            _insert_window(connection, window, stored_at_utc=f"{window.collection_day}T00:00:00Z")


def _pilot_windows(user_id: str, seed: int):
    """Six distinct days of real keyboard+mouse feature windows, reclassified
    as PILOT provenance -- enough for a 3/2/1 TRAIN/VALIDATION/EVALUATION
    day-disjoint freeze under ``config/collection.pilot.yaml``, and well
    above the 20-window enrollment minimum on the TRAIN partition alone.
    """

    ml_config = load_ml_config()
    windows = generate_multiday_user_windows(
        user_id,
        base_seed=seed,
        config=ml_config,
        num_days=6,
        segments_per_day=1,
        segment_minutes=5,
    )
    return [w.model_copy(update={"provenance": Provenance.PILOT}) for w in windows]


def _write_administration(path: Path, participant_ids: list[str]) -> None:
    document = {
        "consents": [
            {
                "participant_id": participant_id,
                "protocol_revision": "v1",
                "consented_at": CONSENTED_AT,
            }
            for participant_id in participant_ids
        ],
        "enrollments": [
            {
                "participant_id": participant_id,
                "enrolled_at": CONSENTED_AT,
                "collector_version": "1.0.0",
                "protocol_version": "1.0.0",
            }
            for participant_id in participant_ids
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")


def _insert_active_profile(database: Path, user_id: str) -> None:
    db = SQLiteDatabase(database, busy_timeout_ms=5000)
    validation_json = json.dumps(
        {
            "accepted": True,
            "code": "PRE_EXISTING_TEST_FIXTURE",
            "baseline": {"false_rejection_rate": 0.0, "false_acceptance_rate": 0.0},
            "candidate": {"false_rejection_rate": 0.0, "false_acceptance_rate": 0.0},
        }
    )
    checksum = hashlib.sha256(b"pre-existing-fixture-profile").hexdigest()
    with db.transaction() as connection:
        connection.execute(
            """
            INSERT INTO model_profiles(
                profile_version, user_id, keyboard_artifact_version, mouse_artifact_version,
                aggregate_checksum, validation_json, status, created_at_utc, activated_at_utc
            ) VALUES ('pre-existing-1', ?, 'kbd-v1', 'mouse-v1', ?, ?, 'ACTIVE', ?, ?)
            """,
            (user_id, checksum, validation_json, CONSENTED_AT, CONSENTED_AT),
        )


@pytest.fixture
def enrollment_fixture(tmp_path: Path):
    """Build a temporary pilot-profile database with two participants (a
    second is required so FAR can be computed by cross-evaluation), freeze
    it with the real ``build_freeze``, write administration records, and
    return the keyword arguments ``activate_first_profile`` takes.
    """

    def _make(*, with_active_profile: bool = False) -> dict:
        from tools.collection.eligibility import ConsentRecord, EnrollmentRecord
        from tools.collection.freeze import build_freeze
        from tools.collection.repository import load_window_summaries

        storage_settings = load_storage_settings(
            ROOT / "config/storage.pilot.yaml",
            workspace_root=ROOT,
            environment={"LOCALAPPDATA": str(tmp_path / "localappdata")},
        )
        storage = StorageService.open(storage_settings)
        database = storage_settings.database_path

        participant_ids = ["participant-01", "participant-02"]
        all_windows = []
        for index, participant_id in enumerate(participant_ids):
            all_windows.extend(_pilot_windows(participant_id, seed=index + 1))
        _insert_corpus(database, all_windows)

        if with_active_profile:
            _insert_active_profile(database, "participant-01")

        collection_settings = load_collection_settings(ROOT / "config/collection.pilot.yaml")
        consented_at = datetime.fromisoformat(CONSENTED_AT.replace("Z", "+00:00"))
        consents = {
            participant_id: ConsentRecord(
                participant_id=participant_id,
                protocol_revision="v1",
                consented_at=consented_at,
            )
            for participant_id in participant_ids
        }
        enrollments = {
            participant_id: EnrollmentRecord(
                participant_id=participant_id,
                enrolled_at=consented_at,
                collector_version="1.0.0",
                protocol_version="1.0.0",
            )
            for participant_id in participant_ids
        }

        manifest = tmp_path / "manifest.json"
        window_summaries = load_window_summaries(database)
        build_freeze(
            window_summaries,
            destination=manifest,
            version="enrollment-test-v1",
            consents=consents,
            enrollments=enrollments,
            settings=collection_settings,
            frozen_at=datetime(2026, 1, 10, tzinfo=UTC),
        )

        administration = tmp_path / "administration.json"
        _write_administration(administration, participant_ids)

        return {
            "participant_id": "participant-01",
            "database": database,
            "manifest": manifest,
            "administration": administration,
            "artifact_root": tmp_path / "artifacts",
            "storage": storage,
            "collection_settings": collection_settings,
            "ml_config": load_ml_config(),
            "risk_settings": load_risk_settings(),
        }

    return _make


def test_refuses_a_user_who_already_has_an_active_profile(enrollment_fixture) -> None:
    from ml.training.enrollment import EnrollmentAdmissionError
    from tools.enrollment.activate import activate_first_profile

    context = enrollment_fixture(with_active_profile=True)
    with pytest.raises(EnrollmentAdmissionError, match="PROFILE_ALREADY_ACTIVE"):
        activate_first_profile(**context)


def test_activates_a_profile_from_the_train_partition(enrollment_fixture) -> None:
    from tools.enrollment.activate import activate_first_profile

    profile = activate_first_profile(**enrollment_fixture())
    assert profile.user_id == "participant-01"
    assert profile.validation.code == "ENROLLMENT_INITIAL_PROFILE_NO_BASELINE"


def test_the_evaluation_partition_is_never_loaded(enrollment_fixture, monkeypatch) -> None:
    """Reading EVALUATION during enrollment would contaminate the headline result."""

    from tools.collection import corpus as corpus_module
    from tools.enrollment.activate import activate_first_profile

    requested: list[str] = []
    original = corpus_module.load_frozen_corpus

    def spy(database, manifest, partition):
        requested.append(partition)
        return original(database, manifest, partition)

    monkeypatch.setattr(corpus_module, "load_frozen_corpus", spy)
    activate_first_profile(**enrollment_fixture())
    assert "EVALUATION" not in requested


def test_the_activated_profile_is_readable_by_the_runtime(enrollment_fixture) -> None:
    from backend.app.runtime.profiles import DirectoryProfileProvider
    from tools.enrollment.activate import activate_first_profile

    context = enrollment_fixture()
    activate_first_profile(**context)
    provider = DirectoryProfileProvider(context["storage"], context["artifact_root"])
    assert provider("participant-01") is not None
