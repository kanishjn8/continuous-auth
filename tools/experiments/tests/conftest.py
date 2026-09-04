"""Fixtures for the E1/E2 experiment-driver tests.

Mirrors ``tools/enrollment/tests/test_activate.py`` and
``tools/updates/tests/conftest.py`` (real ``SQLiteDatabase`` writes, real
``build_freeze``, real ``create_evaluation_freeze``) rather than inventing a
new construction style. Two participants are used throughout: ``user_id`` is
the subject of both experiments, ``impostor_id`` supplies the cross-user
impostor data E1 needs for FAR and the "another participant's windows
relabelled" segment E2 injects.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.risk.config import load_risk_settings
from backend.app.storage.config import load_storage_settings
from backend.app.storage.database import SQLiteDatabase
from backend.app.storage.service import StorageService
from backend.app.updates.config import load_update_settings
from ml.features.config import load_config as load_ml_config
from ml.features.schema import Provenance
from ml.tests.conftest import generate_multiday_user_windows
from tools.collection.config import load_collection_settings
from tools.collection.eligibility import ConsentRecord, EnrollmentRecord
from tools.collection.freeze import build_freeze
from tools.collection.repository import load_window_summaries
from tools.evaluation.freeze import create_evaluation_freeze

ROOT = Path(__file__).resolve().parents[3]
CONSENTED_AT = "2025-12-01T00:00:00Z"
CONSENTED_AT_DT = datetime.fromisoformat(CONSENTED_AT.replace("Z", "+00:00"))
USER_ID = "participant-01"
IMPOSTOR_ID = "participant-02"


def _insert_participant(
    connection: sqlite3.Connection, *, user_id: str, session_id: str, segment_id: str
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO users(user_id, state, schema_version, created_at_utc, updated_at_utc) "
        "VALUES (?, 'ACTIVE', '1.0.0', ?, ?)",
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
            None
            if window.keyboard_features is None
            else json.dumps(window.keyboard_features.model_dump(mode="json")),
            None
            if window.mouse_features is None
            else json.dumps(window.mouse_features.model_dump(mode="json")),
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


def _participant_windows(user_id: str, seed: int, *, num_days: int, segment_minutes: int):
    """``num_days`` distinct days, one combined segment, PILOT provenance.

    All days -- TRAIN, VALIDATION, and EVALUATION alike -- share one
    segment_id/session_id (mirrors ``tools/updates/tests/conftest.py``):
    segment identity does not gate scoring, only training, and the corpus
    loader already separates windows by partition independently of segment.
    """

    ml_config = load_ml_config()
    windows = generate_multiday_user_windows(
        user_id,
        base_seed=seed,
        config=ml_config,
        num_days=num_days,
        segments_per_day=1,
        segment_minutes=segment_minutes,
    )
    segment_id = f"{user_id}-segment"
    session_id = f"{user_id}-session"
    return [
        w.model_copy(
            update={
                "provenance": Provenance.PILOT,
                "segment_id": segment_id,
                "session_id": session_id,
            }
        )
        for w in windows
    ]


def _aggregate_checksum(profile_version, keyboard_artifact, mouse_artifact) -> str:
    import hashlib

    values = (
        profile_version,
        keyboard_artifact.version if keyboard_artifact else "",
        keyboard_artifact.checksum if keyboard_artifact else "",
        mouse_artifact.version if mouse_artifact else "",
        mouse_artifact.checksum if mouse_artifact else "",
    )
    return hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()


def _activate_baseline_profile(*, database: Path, artifact_root: Path, ml_config, user_id: str) -> None:
    """Activate a genuine, pre-existing ACTIVE profile for E2's fixture only.

    E2 needs a baseline already active before its scheduled update run
    (``tools.updates.run.build_candidate`` validates a candidate against
    whatever is currently ACTIVE) -- standing in for the real enrollment
    (design Section 8.4) that would have produced one before any poisoning
    experiment ran. Uses SYNTHETIC-provenance windows, which legitimately
    bypass both the enrollment and promotion gates (development data,
    PLAN.md ADR-006): appropriate here because this is fixture setup for a
    profile that pre-dates the experiment, not the promoted-candidate path
    under test. Mirrors ``tools/updates/tests/conftest.py``.
    """

    from ml.training.isolation_forest import train_user_profile
    from ml.training.persistence import save_artifact

    baseline_windows = generate_multiday_user_windows(
        user_id, base_seed=9001, config=ml_config, num_days=6, segments_per_day=1, segment_minutes=5
    )
    profile = train_user_profile(user_id, baseline_windows, ml_config)
    if not profile:
        raise AssertionError("fixture setup failed to train a baseline profile")

    keyboard_artifact = profile.get("keyboard")
    mouse_artifact = profile.get("mouse")
    if keyboard_artifact is not None:
        save_artifact(keyboard_artifact, artifact_root / user_id / f"{keyboard_artifact.version}.joblib")
    if mouse_artifact is not None:
        save_artifact(mouse_artifact, artifact_root / user_id / f"{mouse_artifact.version}.joblib")

    profile_version = "e2-pre-existing-baseline"
    aggregate_checksum = _aggregate_checksum(profile_version, keyboard_artifact, mouse_artifact)
    validation_json = json.dumps(
        {
            "accepted": True,
            "code": "PRE_EXISTING_TEST_FIXTURE",
            "baseline": {"false_rejection_rate": 0.0, "false_acceptance_rate": 0.0},
            "candidate": {"false_rejection_rate": 0.0, "false_acceptance_rate": 0.0},
        }
    )
    db = SQLiteDatabase(database, busy_timeout_ms=5000)
    with db.transaction() as connection:
        connection.execute(
            """
            INSERT INTO model_profiles(
                profile_version, user_id, keyboard_artifact_version, mouse_artifact_version,
                aggregate_checksum, validation_json, status, created_at_utc, activated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
            """,
            (
                profile_version,
                user_id,
                keyboard_artifact.version if keyboard_artifact else None,
                mouse_artifact.version if mouse_artifact else None,
                aggregate_checksum,
                validation_json,
                CONSENTED_AT,
                CONSENTED_AT,
            ),
        )


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


@pytest.fixture
def experiment_corpus(tmp_path: Path):
    """Build a temporary pilot database/corpus/freeze shared by E1 and E2.

    Returns a dict of the pieces both drivers need: the two participants'
    ids, the storage/database/manifest/administration paths, loaded config
    settings, and an injected impostor segment (participant-02's day-zero
    windows, relabelled as participant-01's) already written into the
    database under a distinct segment_id -- "another participant's windows
    relabelled", per PLAN.md Section 12.4's E2 description.

    Nine days per participant is deliberate: five of them land in TRAIN
    (config/collection.pilot.yaml's 0.60/0.20/0.20 split), which is enough
    to hold out an "early" three-day subset for E1's frozen profile while
    leaving later TRAIN days for the profile the Model Update Manager
    subsequently produces.
    """

    storage_settings = load_storage_settings(
        ROOT / "config/storage.pilot.yaml",
        workspace_root=ROOT,
        environment={"LOCALAPPDATA": str(tmp_path / "localappdata")},
    )
    storage = StorageService.open(storage_settings)
    database = storage_settings.database_path
    artifact_root = tmp_path / "artifacts"

    participant_ids = [USER_ID, IMPOSTOR_ID]
    user_windows = _participant_windows(USER_ID, seed=1, num_days=9, segment_minutes=15)
    impostor_windows = _participant_windows(IMPOSTOR_ID, seed=2, num_days=9, segment_minutes=15)

    # E2's injected segment: participant-02's first day, relabelled as
    # participant-01's, under a distinct segment_id, landing on
    # participant-01's own first collection day (both participants start at
    # the same synthetic base date) so it falls inside participant-01's own
    # TRAIN partition.
    first_day = impostor_windows[0].collection_day
    injected_segment_id = f"{USER_ID}-injected-segment"
    injected_session_id = f"{USER_ID}-injected-session"
    injected_windows = [
        w.model_copy(
            update={
                "window_id": f"injected-{w.window_id}",
                "user_id": USER_ID,
                "session_id": injected_session_id,
                "segment_id": injected_segment_id,
            }
        )
        for w in impostor_windows
        if w.collection_day == first_day
    ]
    if not injected_windows:
        raise AssertionError("fixture setup produced no injected windows")

    _insert_corpus(database, user_windows + impostor_windows + injected_windows)

    collection_settings = load_collection_settings(ROOT / "config/collection.pilot.yaml")
    consents = {
        participant_id: ConsentRecord(
            participant_id=participant_id,
            protocol_revision="v1",
            consented_at=CONSENTED_AT_DT,
        )
        for participant_id in participant_ids
    }
    enrollments = {
        participant_id: EnrollmentRecord(
            participant_id=participant_id,
            enrolled_at=CONSENTED_AT_DT,
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
        version="experiment-test-v1",
        consents=consents,
        enrollments=enrollments,
        settings=collection_settings,
        frozen_at=datetime(2026, 6, 1, tzinfo=UTC),
    )

    administration = tmp_path / "administration.json"
    _write_administration(administration, participant_ids)

    return {
        "user_id": USER_ID,
        "impostor_id": IMPOSTOR_ID,
        "injected_segment_id": injected_segment_id,
        "database": database,
        "manifest": manifest,
        "administration": administration,
        "artifact_root": artifact_root,
        "storage": storage,
        "collection_settings": collection_settings,
        "ml_config": load_ml_config(),
        "risk_settings": load_risk_settings(),
        "update_settings": load_update_settings(),
    }


@pytest.fixture
def evaluation_freeze_fixture(tmp_path: Path):
    """Return a factory: build (and optionally tamper) an evaluation freeze.

    The frozen config is a *copy* of ``config/evaluation.development.yaml``
    under a temporary config directory -- never the real repository file --
    so that a tampering test can corrupt it without touching a real
    ``*.development.yaml``.
    """

    def _make(*, dataset_manifest: Path, tamper: bool = False) -> dict:
        config_directory = tmp_path / "evaluation-config"
        config_directory.mkdir(exist_ok=True)
        frozen_config = config_directory / "evaluation.development.yaml"
        frozen_config.write_text(
            (ROOT / "config/evaluation.development.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        code_revision = "experiment-test-revision"
        evaluation_freeze = tmp_path / "evaluation-freeze.json"
        create_evaluation_freeze(
            evaluation_freeze,
            config_paths=[frozen_config],
            dataset_manifest=dataset_manifest,
            code_revision=code_revision,
            frozen_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
        if tamper:
            frozen_config.write_text(
                frozen_config.read_text(encoding="utf-8") + "\ntampered: true\n",
                encoding="utf-8",
            )
        return {
            "evaluation_freeze": evaluation_freeze,
            "config_directory": config_directory,
            "dataset_manifest": dataset_manifest,
            "code_revision": code_revision,
        }

    return _make


@pytest.fixture
def experiment_fixture(experiment_corpus, evaluation_freeze_fixture):
    """Return a factory ``experiment_fixture(*, tamper_config=False, ...) -> dict``.

    The dict is exactly the keyword arguments both
    ``tools.experiments.drift.run_drift_benefit`` and
    ``tools.experiments.poisoning.run_poisoning_resistance`` accept -- the
    two drivers replay the same frozen-corpus context (design Section 8.6),
    so one fixture serves both.

    ``with_baseline_profile`` activates a pre-existing ACTIVE profile for
    ``user_id`` before returning -- required by E2 (its scheduled update
    run validates a candidate against whatever is currently ACTIVE) but
    deliberately *not* set by E1, which activates its own "frozen" profile
    as part of the experiment and would otherwise be refused by the
    enrollment admission gate (``PROFILE_ALREADY_ACTIVE``).
    """

    def _make(*, tamper_config: bool = False, with_baseline_profile: bool = False) -> dict:
        if with_baseline_profile:
            _activate_baseline_profile(
                database=experiment_corpus["database"],
                artifact_root=experiment_corpus["artifact_root"],
                ml_config=experiment_corpus["ml_config"],
                user_id=experiment_corpus["user_id"],
            )
        freeze_context = evaluation_freeze_fixture(
            dataset_manifest=experiment_corpus["manifest"], tamper=tamper_config
        )
        return {**experiment_corpus, **freeze_context}

    return _make
