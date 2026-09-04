"""`update_fixture`: a temporary pilot database, frozen corpus, an already-
active baseline profile, and a quarantined promotable candidate, for
`tools.updates.run` tests.

Mirrors `tools/enrollment/tests/test_activate.py`'s fixture construction
(real `SQLiteDatabase` writes, real `build_freeze`) but adds what enrollment
does not need: a pre-existing ACTIVE profile trained from real (bypass-
eligible) synthetic windows and persisted as real artifact files, plus an
`UpdateCandidate` produced by a real `UpdateManager.submit_segment` call so
that `run_update` has something genuine to reassess and, once quarantine has
elapsed, promote.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.decisions import VerificationRecord
from backend.app.storage.config import load_storage_settings
from backend.app.storage.database import SQLiteDatabase
from backend.app.storage.service import StorageService
from backend.app.updates.manager import SegmentEvidence, UpdateManager
from backend.app.updates.repository import SQLiteUpdateRepository
from ml.features.config import load_config as load_ml_config
from ml.features.schema import Provenance
from ml.tests.conftest import generate_multiday_user_windows
from ml.training.isolation_forest import train_user_profile
from ml.training.persistence import save_artifact
from protocol.generated.python.contracts import RiskLevel, VerificationAnchor
from tools.collection.config import load_collection_settings
from tools.collection.eligibility import ConsentRecord, EnrollmentRecord
from tools.collection.freeze import build_freeze
from tools.collection.repository import load_window_summaries

ROOT = Path(__file__).resolve().parents[3]
CONSENTED_AT = "2025-12-01T00:00:00Z"
CONSENTED_AT_DT = datetime.fromisoformat(CONSENTED_AT.replace("Z", "+00:00"))


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
    """Six distinct days, one segment spanning all of them, PILOT provenance.

    Six days matches `tools/enrollment/tests/test_activate.py`'s fixture,
    which documents the resulting split as day-disjoint 3 TRAIN / 2
    VALIDATION / 1 EVALUATION under `config/collection.pilot.yaml`'s
    0.60/0.20/0.20 fractions -- comfortably above both
    `min_promotable_windows` (10) and `min_baseline_windows` (20) on the
    TRAIN partition alone. All windows share one `segment_id` so the whole
    round is exactly the one segment a scheduled update promotes.
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
    values = (
        profile_version,
        keyboard_artifact.version if keyboard_artifact else "",
        keyboard_artifact.checksum if keyboard_artifact else "",
        mouse_artifact.version if mouse_artifact else "",
        mouse_artifact.checksum if mouse_artifact else "",
    )
    return hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()


def _activate_baseline_profile(
    *, database: Path, artifact_root: Path, ml_config, user_id: str
) -> None:
    """Train and persist a genuine, already-ACTIVE baseline profile.

    Uses SYNTHETIC-provenance windows, which bypass both the enrollment
    admission gate and the promotion gate by design (development data,
    PLAN.md ADR-006) -- appropriate here because this is fixture setup for
    a *pre-existing* profile, not the promoted-candidate path under test.
    Real artifact files are saved and a real aggregate checksum computed so
    that `backend.app.runtime.profiles.DirectoryProfileProvider` -- the same
    reader `tools/updates/run.py::build_candidate` uses -- can load it.
    """

    baseline_windows = generate_multiday_user_windows(
        user_id, base_seed=9001, config=ml_config, num_days=6, segments_per_day=1, segment_minutes=5
    )
    profile = train_user_profile(user_id, baseline_windows, ml_config)
    if not profile:
        raise AssertionError("fixture setup failed to train a baseline profile")

    keyboard_artifact = profile.get("keyboard")
    mouse_artifact = profile.get("mouse")
    if keyboard_artifact is not None:
        save_artifact(
            keyboard_artifact, artifact_root / user_id / f"{keyboard_artifact.version}.joblib"
        )
    if mouse_artifact is not None:
        save_artifact(mouse_artifact, artifact_root / user_id / f"{mouse_artifact.version}.joblib")

    profile_version = "baseline-1"
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


@pytest.fixture
def update_fixture(tmp_path: Path):
    """Return a factory: `update_fixture(segment_completed_at=...) -> dict`.

    The dict is exactly the keyword arguments `tools.updates.run.run_update`
    takes, minus `scheduled_for` (the tests supply that themselves so they
    can vary it independently of when the segment completed).
    """

    def _make(*, segment_completed_at: datetime) -> dict:
        storage_settings = load_storage_settings(
            ROOT / "config/storage.pilot.yaml",
            workspace_root=ROOT,
            environment={"LOCALAPPDATA": str(tmp_path / "localappdata")},
        )
        storage = StorageService.open(storage_settings)
        database = storage_settings.database_path
        artifact_root = tmp_path / "artifacts"
        ml_config = load_ml_config()

        user_id = "participant-01"
        impostor_id = "participant-02"
        participant_ids = [user_id, impostor_id]

        all_windows = []
        for index, participant_id in enumerate(participant_ids):
            all_windows.extend(_pilot_windows(participant_id, seed=index + 1))
        _insert_corpus(database, all_windows)

        _activate_baseline_profile(
            database=database, artifact_root=artifact_root, ml_config=ml_config, user_id=user_id
        )

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
            version="update-run-test-v1",
            consents=consents,
            enrollments=enrollments,
            settings=collection_settings,
            frozen_at=datetime(2026, 1, 10, tzinfo=UTC),
        )

        segment_id = f"{user_id}-segment"
        session_id = f"{user_id}-session"
        segment_windows = [w for w in all_windows if w.segment_id == segment_id]
        scored_windows = sum(
            1
            for w in segment_windows
            if w.keyboard_features is not None or w.mouse_features is not None
        )

        verification = VerificationRecord(
            anchor_id=f"anchor-{segment_id}",
            user_id=user_id,
            session_id=session_id,
            segment_id=segment_id,
            anchor_type=VerificationAnchor.A3_SCHEDULED_PROMPT,
            evidence_reference="a" * 64,
            authenticated_at=segment_completed_at,
        )
        evidence = SegmentEvidence(
            user_id=user_id,
            session_id=session_id,
            segment_id=segment_id,
            completed_at=segment_completed_at,
            risk_levels=(RiskLevel.LOW,),
            scored_windows=scored_windows,
            enforcement_triggered=False,
            unexplained_gap=False,
            verification=verification,
        )

        from backend.app.updates.config import load_update_settings

        manager = UpdateManager(load_update_settings(), SQLiteUpdateRepository(storage))
        candidate = manager.submit_segment(evidence)
        assert candidate.disposition.value == "QUARANTINED", (
            f"fixture setup did not produce a quarantined candidate: "
            f"{candidate.disposition!r} / {candidate.reason_code!r}"
        )

        return {
            "user_id": user_id,
            "database": database,
            "manifest": manifest,
            "storage": storage,
            "artifact_root": artifact_root,
            "ml_config": ml_config,
        }

    return _make
