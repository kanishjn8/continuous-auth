"""Provenance is decided by the storage profile and by nothing else."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.runtime.cli import _parser
from backend.app.storage.config import StorageSettings, load_storage_settings
from protocol.generated.python.contracts import (
    DataProvenance,
    StorageDataPolicy,
    StorageEnvironment,
)

WORKSPACE = Path(__file__).resolve().parents[2]
PILOT_CONFIG = WORKSPACE / "config" / "storage.pilot.yaml"
DEVELOPMENT_CONFIG = WORKSPACE / "config" / "storage.development.yaml"


def _settings(config: Path, tmp_path: Path) -> StorageSettings:
    return load_storage_settings(
        config,
        workspace_root=WORKSPACE,
        environment={"LOCALAPPDATA": str(tmp_path)},
    )


def test_pilot_profile_is_approved_collection_and_yields_pilot(tmp_path: Path) -> None:
    settings = _settings(PILOT_CONFIG, tmp_path)
    assert settings.environment is StorageEnvironment.PILOT
    assert settings.data_policy is StorageDataPolicy.APPROVED_COLLECTION
    assert settings.synthetic_only is False
    assert settings.collection_provenance is DataProvenance.PILOT
    assert settings.retention.pilot_mode is True


def test_development_profile_still_yields_synthetic(tmp_path: Path) -> None:
    """Requirement: existing synthetic development capability is untouched."""

    settings = _settings(DEVELOPMENT_CONFIG, tmp_path)
    assert settings.data_policy is StorageDataPolicy.SYNTHETIC_ONLY
    assert settings.synthetic_only is True
    assert settings.collection_provenance is DataProvenance.SYNTHETIC


def test_the_two_profiles_never_share_a_directory(tmp_path: Path) -> None:
    """Pilot data must not land on top of previously collected synthetic data."""

    pilot = _settings(PILOT_CONFIG, tmp_path)
    development = _settings(DEVELOPMENT_CONFIG, tmp_path)
    assert pilot.root_directory != development.root_directory
    assert pilot.database_path != development.database_path


def test_pilot_retention_outlives_a_collection_round(tmp_path: Path) -> None:
    """Early collection days must survive until the corpus is frozen."""

    pilot = _settings(PILOT_CONFIG, tmp_path)
    development = _settings(DEVELOPMENT_CONFIG, tmp_path)
    assert pilot.retention.feature_window_days > development.retention.feature_window_days
    assert pilot.retention.feature_window_days >= 30


def test_a_normal_run_defaults_to_the_pilot_profile() -> None:
    """Requirement: real collection needs no source change and no extra flag."""

    arguments = _parser().parse_args(
        ["--participant-id", "participant-01", "--artifact-root", "models"]
    )
    assert arguments.storage_config.name == "storage.pilot.yaml"
    assert arguments.participant_id == "participant-01"


def test_the_previous_flag_name_still_works() -> None:
    arguments = _parser().parse_args(
        ["--synthetic-user", "participant-01", "--artifact-root", "models"]
    )
    assert arguments.participant_id == "participant-01"


def test_choosing_synthetic_requires_saying_so_explicitly() -> None:
    arguments = _parser().parse_args(
        [
            "--participant-id",
            "participant-01",
            "--artifact-root",
            "models",
            "--storage-config",
            str(DEVELOPMENT_CONFIG),
        ]
    )
    assert arguments.storage_config == DEVELOPMENT_CONFIG


@pytest.mark.parametrize(
    ("config", "rejected"),
    [
        (PILOT_CONFIG, DataProvenance.SYNTHETIC),
        (DEVELOPMENT_CONFIG, DataProvenance.PILOT),
    ],
)
def test_each_store_refuses_the_other_kind_of_data(
    config: Path, rejected: DataProvenance, tmp_path: Path
) -> None:
    """The mismatch is refused in both directions, not just one."""

    from backend.app.storage.errors import StorageUnavailableError
    from backend.app.storage.service import StorageService

    settings = _settings(config, tmp_path)
    service = StorageService.__new__(StorageService)
    service.settings = settings
    with pytest.raises(StorageUnavailableError):
        service._enforce_provenance(rejected)
    service._enforce_provenance(settings.collection_provenance)


def test_session_entry_evidence_is_not_labelled_synthetic() -> None:
    """A pilot session's A1 anchor must not carry synthetic-run wording.

    The anchor is the evidence a later promotion decision rests on, so a
    provenance word baked into it is a data-integrity problem, not cosmetics.
    """

    source = (WORKSPACE / "backend" / "app" / "runtime" / "application.py").read_text(
        encoding="utf-8"
    )
    assert "synthetic-entry-" not in source
    assert "session-entry-" in source


def test_provenance_endpoint_reports_the_active_store(tmp_path: Path) -> None:
    """An operator must be able to confirm what a run is recording."""

    from backend.app.api.backend import SQLiteApiBackend
    from backend.app.storage.service import StorageService

    settings = _settings(PILOT_CONFIG, tmp_path)
    backend = SQLiteApiBackend(StorageService.open(settings), active_user_provider=lambda: None)
    reported = backend.collection_provenance()
    assert reported["environment"] == "PILOT"
    assert reported["data_policy"] == "APPROVED_COLLECTION"
    assert reported["collection_provenance"] == "PILOT"
    assert reported["config_version"] == settings.config_version


def test_provenance_endpoint_reports_synthetic_for_development(tmp_path: Path) -> None:
    from backend.app.api.backend import SQLiteApiBackend
    from backend.app.storage.service import StorageService

    settings = _settings(DEVELOPMENT_CONFIG, tmp_path)
    backend = SQLiteApiBackend(StorageService.open(settings), active_user_provider=lambda: None)
    assert backend.collection_provenance()["collection_provenance"] == "SYNTHETIC"
