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


def _build_fixture(tmp_path: Path, participant_ids: list[str], *, with_active_profile: bool):
    """Build a temporary pilot-profile database, freeze it with the real
    ``build_freeze``, write administration records, and return the keyword
    arguments ``activate_first_profile`` takes.

    ``participant_ids`` is the whole cohort. With two entries the frozen
    manifest contains impostor evidence and activation takes its measured-FAR
    path; with one it contains none and activation must record FAR as
    unmeasured (ADR-014). The two paths are selected by the data alone.
    """

    def _make() -> dict:
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


@pytest.fixture
def enrollment_fixture(tmp_path: Path):
    """Two participants: the frozen manifest contains impostor evidence."""

    def _make(*, with_active_profile: bool = False) -> dict:
        return _build_fixture(
            tmp_path,
            ["participant-01", "participant-02"],
            with_active_profile=with_active_profile,
        )()

    return _make


@pytest.fixture
def single_participant_fixture(tmp_path: Path):
    """One participant: the frozen manifest contains no impostor at all.

    This is the delivered study's shape (ADR-014). Activation must succeed,
    measure a real FRR, and record FAR as unmeasured.
    """

    def _make(*, with_active_profile: bool = False) -> dict:
        return _build_fixture(
            tmp_path, ["participant-01"], with_active_profile=with_active_profile
        )()

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


def test_activates_a_single_participant_profile(single_participant_fixture) -> None:
    """The delivered study's shape: one participant, no cohort, no impostor.

    Activation must succeed on the strength of the ADR-013 enrollment gate
    and a real FRR measurement (ADR-014).
    """
    from tools.enrollment.activate import activate_first_profile

    profile = activate_first_profile(**single_participant_fixture())
    assert profile.user_id == "participant-01"
    assert profile.validation.accepted is True
    assert profile.validation.code == "ENROLLMENT_INITIAL_PROFILE_NO_IMPOSTOR_COHORT"
    assert profile.validation.candidate.false_acceptance_rate is None
    assert 0.0 <= profile.validation.candidate.false_rejection_rate <= 1.0
    assert "unmeasured" in profile.validation.operating_point
    assert "risk.medium_threshold" in profile.validation.operating_point


def test_far_is_never_fabricated_as_zero(single_participant_fixture) -> None:
    """`None` means NOT MEASURED. `0.0` would be a claim nobody measured."""
    from tools.enrollment.activate import activate_first_profile

    profile = activate_first_profile(**single_participant_fixture())
    far = profile.validation.candidate.false_acceptance_rate
    assert far is None
    assert far != 0.0
    assert profile.validation.baseline.false_acceptance_rate is None


def test_single_participant_profile_round_trips_a_null_far(single_participant_fixture) -> None:
    """A JSON null FAR must survive model_profiles without a migration."""
    from backend.app.updates.repository import _profile_from_row
    from tools.enrollment.activate import activate_first_profile

    context = single_participant_fixture()
    activate_first_profile(**context)
    with SQLiteDatabase(context["database"], busy_timeout_ms=5000).connection() as connection:
        row = connection.execute(
            "SELECT * FROM model_profiles WHERE user_id = ? AND status = 'ACTIVE'",
            ("participant-01",),
        ).fetchone()
    assert row is not None
    stored = _profile_from_row(row)
    assert stored.validation.candidate.false_acceptance_rate is None
    assert stored.validation.code == "ENROLLMENT_INITIAL_PROFILE_NO_IMPOSTOR_COHORT"
    assert "unmeasured" in stored.validation.operating_point


def test_two_participant_corpus_still_measures_far(enrollment_fixture) -> None:
    """The cohort path is unchanged and is selected by the data alone."""
    from tools.enrollment.activate import activate_first_profile

    profile = activate_first_profile(**enrollment_fixture())
    assert profile.validation.code == "ENROLLMENT_INITIAL_PROFILE_NO_BASELINE"
    assert profile.validation.candidate.false_acceptance_rate is not None
    assert "EER" in profile.validation.operating_point


def test_failed_validation_leaves_no_orphaned_artifact(
    single_participant_fixture, monkeypatch
) -> None:
    """A failed activation must not leave a .joblib with no database row."""
    from tools.enrollment import activate as activate_module

    context = single_participant_fixture()

    def _no_genuine(*args, **kwargs):
        raise ValueError("ENROLLMENT_VALIDATION_NO_GENUINE: forced for this test")

    monkeypatch.setattr(activate_module, "_measure_validation_metrics", _no_genuine)
    with pytest.raises(ValueError, match="ENROLLMENT_VALIDATION_NO_GENUINE"):
        activate_module.activate_first_profile(**context)

    artifact_root = context["artifact_root"]
    written = list(artifact_root.rglob("*.joblib")) if artifact_root.exists() else []
    assert written == []

    with SQLiteDatabase(context["database"], busy_timeout_ms=5000).connection() as connection:
        rows = connection.execute("SELECT COUNT(*) AS n FROM model_profiles").fetchone()
    assert rows["n"] == 0


def test_frr_is_measured_at_the_deployed_operating_point() -> None:
    """FRR must use the same boundary and comparison the live engine uses.

    ``RiskEngine._candidate_level`` breaches when
    ``calibrated_risk >= medium_threshold``, and models map
    ``calibrated_risk = 1 - percentile/100``, so on the percentile scale the
    breach condition is ``percentile <= threshold_pct`` -- inclusive, and on
    the other side of the comparison, because the mapping is decreasing.
    """
    import numpy as np

    from backend.app.models.service import percentile_for_calibrated_risk
    from tools.enrollment.activate import _frr_at_deployed_operating_point

    medium = 0.45
    threshold_pct = percentile_for_calibrated_risk(medium)
    assert threshold_pct == pytest.approx(55.0)

    # Two of five sit at or below 55.0: 10.0, and exactly 55.0.
    percentiles = np.asarray([10.0, 55.0, 55.000001, 80.0, 99.0], dtype=float)
    metrics, operating_point = _frr_at_deployed_operating_point(percentiles, medium)

    assert metrics.false_rejection_rate == pytest.approx(2 / 5)
    assert metrics.false_acceptance_rate is None
    assert "percentile<=55.000" in operating_point
    assert "risk.medium_threshold" in operating_point


def test_a_genuine_score_exactly_on_the_boundary_counts_as_rejected() -> None:
    """The boundary is inclusive, matching ``value >= medium_threshold``."""
    import numpy as np

    from backend.app.models.service import percentile_for_calibrated_risk
    from tools.enrollment.activate import _frr_at_deployed_operating_point

    medium = 0.45
    boundary = percentile_for_calibrated_risk(medium)

    on_boundary, _ = _frr_at_deployed_operating_point(np.asarray([boundary]), medium)
    just_above, _ = _frr_at_deployed_operating_point(np.asarray([boundary + 1e-9]), medium)
    just_below, _ = _frr_at_deployed_operating_point(np.asarray([boundary - 1e-9]), medium)

    assert on_boundary.false_rejection_rate == pytest.approx(1.0)
    assert just_above.false_rejection_rate == pytest.approx(0.0)
    assert just_below.false_rejection_rate == pytest.approx(1.0)


def test_the_operating_point_tracks_a_different_medium_threshold() -> None:
    """The boundary is read from config, never hardcoded."""
    import numpy as np

    from tools.enrollment.activate import _frr_at_deployed_operating_point

    percentiles = np.asarray([10.0, 30.0, 50.0, 70.0, 90.0], dtype=float)
    strict, _ = _frr_at_deployed_operating_point(percentiles, 0.75)  # percentile <= 25
    lenient, _ = _frr_at_deployed_operating_point(percentiles, 0.25)  # percentile <= 75
    assert strict.false_rejection_rate == pytest.approx(1 / 5)
    assert lenient.false_rejection_rate == pytest.approx(4 / 5)


def test_refuses_activation_with_no_genuine_validation_scores() -> None:
    """No scorable VALIDATION window is a data problem, not a cohort problem.

    The guard is on the fused per-window evidence, which is what the headline
    rate is measured over -- an empty profile produces no fused score, so the
    refusal fires before any per-modality pooling is attempted.
    """
    from tools.enrollment import activate as activate_module

    with pytest.raises(ValueError, match="ENROLLMENT_VALIDATION_NO_GENUINE"):
        activate_module._measure_validation_metrics(
            "participant-01",
            {},
            {},
            medium_threshold=0.45,
            keyboard_weight=0.5,
            mouse_weight=0.5,
        )


# ---------------------------------------------------------------------------
# Reporting unit: the headline FRR is measured in the engine's breach unit
# ---------------------------------------------------------------------------


class _StubScore:
    """Minimal stand-in for ``ml.training.common.ScoreResult``."""

    def __init__(self, percentile: float | None) -> None:
        self.available = percentile is not None
        self.percentile_score = percentile


def _stub_scorer(monkeypatch, by_modality_and_window: dict[tuple[str, str], float | None]):
    """Patch ``score_window`` so the fusion arithmetic can be asserted exactly."""

    from tools.enrollment import activate as activate_module

    def fake_score_window(artifact, window):
        return _StubScore(by_modality_and_window.get((artifact, window)))

    monkeypatch.setattr(activate_module, "score_window", fake_score_window)


def test_fused_window_percentiles_yield_one_value_per_scorable_window(monkeypatch) -> None:
    """One fused value per window -- not one per (window, modality) pair."""

    from tools.enrollment.activate import _fused_window_percentiles

    _stub_scorer(
        monkeypatch,
        {
            ("kbd-artifact", "full-window"): 20.0,
            ("mouse-artifact", "full-window"): 60.0,
            ("kbd-artifact", "kbd-window"): 40.0,
            ("mouse-artifact", "kbd-window"): None,
            ("kbd-artifact", "mouse-window"): None,
            ("mouse-artifact", "mouse-window"): 80.0,
            ("kbd-artifact", "empty-window"): None,
            ("mouse-artifact", "empty-window"): None,
        },
    )

    fused = _fused_window_percentiles(
        {"keyboard": "kbd-artifact", "mouse": "mouse-artifact"},
        ["full-window", "kbd-window", "mouse-window", "empty-window"],
        keyboard_weight=0.5,
        mouse_weight=0.5,
    )

    # Three scorable windows, one fused value each. The window that scored in
    # neither modality contributes nothing rather than a zero (ADR-005).
    assert fused == pytest.approx([40.0, 40.0, 80.0])


def test_fusion_is_availability_renormalised_and_uses_the_configured_weights(
    monkeypatch,
) -> None:
    """A single-modality window must not be penalised for the missing one."""

    from tools.enrollment.activate import _fused_window_percentiles

    _stub_scorer(
        monkeypatch,
        {
            ("kbd-artifact", "full-window"): 20.0,
            ("mouse-artifact", "full-window"): 60.0,
            ("kbd-artifact", "kbd-window"): 20.0,
            ("mouse-artifact", "kbd-window"): None,
        },
    )

    fused = _fused_window_percentiles(
        {"keyboard": "kbd-artifact", "mouse": "mouse-artifact"},
        ["full-window", "kbd-window"],
        keyboard_weight=0.25,
        mouse_weight=0.75,
    )

    # FULL honours the weights; KBD_ONLY renormalises to the keyboard score
    # itself rather than diluting it toward zero.
    assert fused[0] == pytest.approx(0.25 * 20.0 + 0.75 * 60.0)
    assert fused[1] == pytest.approx(20.0)


def test_a_single_modality_profile_still_fuses(monkeypatch) -> None:
    """A profile where only one modality trained must still measure."""

    from tools.enrollment.activate import _fused_window_percentiles

    _stub_scorer(monkeypatch, {("mouse-artifact", "mouse-window"): 30.0})

    fused = _fused_window_percentiles(
        {"mouse": "mouse-artifact"},
        ["mouse-window"],
        keyboard_weight=0.5,
        mouse_weight=0.5,
    )
    assert fused == pytest.approx([30.0])


def test_headline_frr_is_the_fused_per_window_rate(single_participant_fixture) -> None:
    """The reported FRR must equal the rate RiskEngine would actually breach at.

    Recomputed independently here from the activated artifacts, so the test
    fails if the headline silently reverts to the pooled per-(window,modality)
    unit that diagnosis.md flagged (0.8195 pooled vs 0.8418 fused in the pilot).
    """
    import numpy as np

    from backend.app.models.service import percentile_for_calibrated_risk
    from ml.training.persistence import load_artifact
    from tools.collection import corpus as corpus_module
    from tools.enrollment.activate import _fused_window_percentiles, activate_first_profile

    context = single_participant_fixture()
    profile = activate_first_profile(**context)

    artifacts = {}
    if profile.keyboard_artifact_version:
        artifacts["keyboard"] = load_artifact(
            context["artifact_root"]
            / "participant-01"
            / f"{profile.keyboard_artifact_version}.joblib"
        )
    if profile.mouse_artifact_version:
        artifacts["mouse"] = load_artifact(
            context["artifact_root"] / "participant-01" / f"{profile.mouse_artifact_version}.joblib"
        )

    validation = corpus_module.load_frozen_corpus(
        context["database"], context["manifest"], "VALIDATION"
    )
    risk = context["risk_settings"].risk
    fused = _fused_window_percentiles(
        artifacts,
        validation.windows_by_user["participant-01"],
        keyboard_weight=risk.keyboard_weight,
        mouse_weight=risk.mouse_weight,
    )
    boundary = percentile_for_calibrated_risk(risk.medium_threshold)
    expected = float(np.mean(np.asarray(fused) <= boundary))

    assert profile.validation.candidate.false_rejection_rate == pytest.approx(expected)


def test_the_operating_point_names_the_secondary_per_modality_rate(
    single_participant_fixture,
) -> None:
    """The per-modality figure is kept, but labelled as secondary."""
    from tools.enrollment.activate import activate_first_profile

    profile = activate_first_profile(**single_participant_fixture())
    operating_point = profile.validation.operating_point

    assert "fused" in operating_point
    assert "secondary per-(window,modality) frr@" in operating_point
    assert "modality-scores" in operating_point


def test_the_secondary_rate_is_measured_over_more_samples_than_the_headline(
    single_participant_fixture,
) -> None:
    """The two units are structurally different, not a relabelling.

    Every FULL window contributes two modality-scores but only one fused
    window, so as long as the corpus contains a FULL window the secondary
    sample count strictly exceeds the headline's.
    """
    import re

    from tools.enrollment.activate import activate_first_profile

    profile = activate_first_profile(**single_participant_fixture())
    operating_point = profile.validation.operating_point

    fused_match = re.search(r"over (\d+) windows", operating_point)
    pooled_match = re.search(r"over (\d+) modality-scores", operating_point)
    assert fused_match is not None and pooled_match is not None
    assert int(pooled_match.group(1)) > int(fused_match.group(1))


def test_the_secondary_rate_survives_into_model_profiles(single_participant_fixture) -> None:
    """The audit string is persisted, not just printed."""
    from backend.app.updates.repository import _profile_from_row
    from tools.enrollment.activate import activate_first_profile

    context = single_participant_fixture()
    activate_first_profile(**context)
    with SQLiteDatabase(context["database"], busy_timeout_ms=5000).connection() as connection:
        row = connection.execute(
            "SELECT * FROM model_profiles WHERE user_id = ? AND status = 'ACTIVE'",
            ("participant-01",),
        ).fetchone()
    stored = _profile_from_row(row)
    assert "secondary per-(window,modality) frr@" in stored.validation.operating_point


def test_the_cohort_path_also_reports_in_the_fused_unit(enrollment_fixture) -> None:
    """Both measurement paths must report FRR in the same unit."""
    from tools.enrollment.activate import activate_first_profile

    profile = activate_first_profile(**enrollment_fixture())
    assert profile.validation.code == "ENROLLMENT_INITIAL_PROFILE_NO_BASELINE"
    assert "fused-per-window EER" in profile.validation.operating_point
    assert "secondary per-(window,modality) frr@" in profile.validation.operating_point


# ---------------------------------------------------------------------------
# Provenance: activation registers its artifacts in the models table
# ---------------------------------------------------------------------------


def _models_rows(database: Path) -> list:
    with SQLiteDatabase(database, busy_timeout_ms=5000).connection() as connection:
        return connection.execute("SELECT * FROM models ORDER BY modality").fetchall()


def test_activation_registers_every_artifact_in_the_models_table(
    single_participant_fixture,
) -> None:
    """The models table is the database's record of what is actually deployed."""
    from tools.enrollment.activate import activate_first_profile

    context = single_participant_fixture()
    profile = activate_first_profile(**context)

    registered = {row["modality"]: row for row in _models_rows(context["database"])}
    expected = {
        name: version
        for name, version in (
            ("KEYBOARD", profile.keyboard_artifact_version),
            ("MOUSE", profile.mouse_artifact_version),
        )
        if version is not None
    }

    assert expected, "the fixture must train at least one modality"
    assert set(registered) == set(expected)
    for modality, version in expected.items():
        assert registered[modality]["artifact_version"] == version
        assert registered[modality]["user_id"] == "participant-01"


def test_registered_rows_carry_the_artifact_checksum_and_training_range(
    single_participant_fixture,
) -> None:
    """An audit must be able to check the .joblib against the database."""
    from ml.features.schema import FEATURE_SCHEMA_VERSION
    from ml.training.persistence import load_artifact
    from tools.enrollment.activate import activate_first_profile

    context = single_participant_fixture()
    profile = activate_first_profile(**context)
    rows = {row["modality"]: row for row in _models_rows(context["database"])}

    for modality, version in (
        ("KEYBOARD", profile.keyboard_artifact_version),
        ("MOUSE", profile.mouse_artifact_version),
    ):
        if version is None:
            continue
        artifact = load_artifact(context["artifact_root"] / "participant-01" / f"{version}.joblib")
        row = rows[modality]
        assert row["checksum"] == artifact.checksum
        assert row["feature_schema_version"] == FEATURE_SCHEMA_VERSION
        assert row["training_range_start"] == artifact.training_data_date_range[0]
        assert row["training_range_end"] == artifact.training_data_date_range[1]


def test_registered_rows_record_pilot_provenance(single_participant_fixture) -> None:
    """Provenance comes from the training windows, never from a default."""
    from tools.enrollment.activate import activate_first_profile

    context = single_participant_fixture()
    activate_first_profile(**context)

    rows = _models_rows(context["database"])
    assert rows
    for row in rows:
        payload = json.loads(row["artifact_json"])
        assert payload["provenance"] == ["PILOT"]
        # The calibration reference distribution travels with the record, so
        # the checksum can be verified without opening the .joblib.
        assert payload["calibration"]["reference_scores"]


def test_a_failed_activation_registers_no_model(single_participant_fixture, monkeypatch) -> None:
    """Registration happens with the artifact write, after validation passed."""
    from tools.enrollment import activate as activate_module

    context = single_participant_fixture()

    def _no_genuine(*args, **kwargs):
        raise ValueError("ENROLLMENT_VALIDATION_NO_GENUINE: forced for this test")

    monkeypatch.setattr(activate_module, "_measure_validation_metrics", _no_genuine)
    with pytest.raises(ValueError, match="ENROLLMENT_VALIDATION_NO_GENUINE"):
        activate_module.activate_first_profile(**context)

    assert _models_rows(context["database"]) == []


def test_the_registry_record_refuses_a_non_deployable_modality() -> None:
    """A 'combined' artifact is an ADR-006 baseline, never a deployed model."""
    from ml.calibration.percentile import PercentileCalibrator
    from ml.training.common import ModelArtifact, PreprocessingParams
    from tools.enrollment.activate import _registry_record

    artifact = ModelArtifact(
        user_id="participant-01",
        modality="combined",
        model_type="isolation_forest",
        feature_schema_version="1.0.0",
        feature_names=["a"],
        training_data_date_range=("2026-01-01", "2026-01-02"),
        provenance_mix={"PILOT": 1},
        hyperparameters={},
        preprocessing=PreprocessingParams(mean=[0.0], scale=[1.0]),
        calibration=PercentileCalibrator(reference_scores=[0.0]),
        metrics_at_training={},
        version="v1",
        checksum="a" * 64,
    )
    with pytest.raises(ValueError, match="models table records deployed"):
        _registry_record(artifact)
