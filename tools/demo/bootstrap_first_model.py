"""DEMO/DEV-ONLY: train and activate a user's very first model profile.

Why this script exists
-----------------------
The live runtime (``backend/app/runtime/orchestrator.py::_progress_state``)
never advances a user out of ``ENROLLING`` unless
``DirectoryProfileProvider`` already resolves an ``ACTIVE`` row in
``model_profiles`` for that user -- see ``backend/app/runtime/profiles.py``.
The only code path that writes that row (``UpdateManager.run_scheduled`` in
``backend/app/updates/manager.py``, via
``SQLiteUpdateRepository.activate_profile``) is never called from any
production code: it exists solely to *replace* an already-active model
after its G1-G6 promotion gates and 7-day quarantine pass
(``config/updates.development.yaml``). There is no code path anywhere in
this repository that creates the *first* model for a user, so a solo
developer collecting their own data has no way to leave ENROLLING at all,
independent of ``config/risk.development.yaml``'s ``min_distinct_days``.

This script is the missing bootstrap step, built for local, single-user
development only:

1. Read this user's already-collected ``feature_windows`` rows straight out
   of the runtime SQLite database (whatever provenance the live runtime
   stamped them with -- the synthetic-development CLI always stamps
   ``SYNTHETIC``, see ``backend/app/runtime/cli.py`` /
   ``create_synthetic_development_application``).
2. Train both modality models with the same production trainer the rest of
   the system uses (``ml/training/isolation_forest.py::train_user_profile``),
   gated only by ``min_baseline_windows`` (window count, config value in
   ``config/ml.development.yaml``) -- there is no day requirement here.
3. Persist the artifacts and activate them via
   ``SQLiteUpdateRepository.activate_profile``, the exact same call the real
   Model Update Manager uses -- but WITHOUT running it through
   ``run_scheduled``'s G1-G6 gates, because those gates compare a candidate
   against an existing active model and there is none yet.

This intentionally bypasses the reviewed G1-G6 promotion workflow. It
refuses to run if the user already has an ACTIVE profile -- once a real
model is live, all subsequent updates MUST go through
``UpdateManager.run_scheduled`` (not yet wired to an automatic trigger
either; that remains a separate follow-up). The resulting ``ValidationReport``
FAR/FRR fields are structurally required by ``ModelProfile`` but are NOT a
real accuracy measurement: there is no held-out day and no impostor data to
compute them against here. Do not cite them as evidence of anything.
This mirrors the same "demo-only, not used by tests or evaluation" spirit as
``config/risk.demo-live-single-day.yaml``.

Usage (from repo root, with the project installed):

    python tools/demo/bootstrap_first_model.py \
        --user-id synthetic-user \
        --artifact-root "$env:LOCALAPPDATA/ContinuousAuthentication/Development/models" \
        --storage-config config/storage.development.yaml \
        --ml-config config/ml.development.yaml

Run this once, with the backend stopped (SQLite is single-writer-friendly
but simplest to run cold). Then start the backend as usual -- pointed at
``config/risk.demo-live-single-day.yaml`` if you want ENROLLING to clear
today -- and it will pick up the newly-activated profile on the next
``start_authenticated_session`` call.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from backend.app.storage.config import load_storage_settings
from backend.app.storage.service import StorageService
from backend.app.updates.manager import ModelProfile, ValidationMetrics, ValidationReport
from backend.app.updates.repository import SQLiteUpdateRepository
from ml.features.config import load_config as load_ml_config
from ml.features.schema import FeatureWindow
from ml.training.isolation_forest import train_user_profile
from ml.training.persistence import save_artifact

ROOT = Path(__file__).resolve().parents[2]


def _load_windows(storage: StorageService, user_id: str) -> list[FeatureWindow]:
    with storage.database.connection() as connection:
        rows = connection.execute(
            """
            SELECT window_id, user_id, session_id, segment_id, t_start_us, t_end_us,
                   quality_label, key_event_count, mouse_event_count, collection_day,
                   provenance, keyboard_features_json, mouse_features_json, context_json,
                   schema_version
            FROM feature_windows WHERE user_id = ?
            """,
            (user_id,),
        ).fetchall()
    windows: list[FeatureWindow] = []
    for row in rows:
        windows.append(
            FeatureWindow.model_validate(
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
        )
    return windows


def _aggregate_checksum(
    profile_version: str,
    keyboard_version: str | None,
    keyboard_checksum: str | None,
    mouse_version: str | None,
    mouse_checksum: str | None,
) -> str:
    import hashlib

    values = (
        profile_version,
        keyboard_version or "",
        keyboard_checksum or "",
        mouse_version or "",
        mouse_checksum or "",
    )
    return hashlib.sha256("\x00".join(values).encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument(
        "--storage-config", type=Path, default=ROOT / "config/storage.development.yaml"
    )
    parser.add_argument("--ml-config", type=Path, default=ROOT / "config/ml.development.yaml")
    args = parser.parse_args(argv)

    storage = StorageService.open(
        load_storage_settings(args.storage_config, workspace_root=ROOT)
    )

    with storage.database.connection() as connection:
        existing = connection.execute(
            "SELECT profile_version FROM model_profiles WHERE user_id = ? AND status = 'ACTIVE'",
            (args.user_id,),
        ).fetchone()
    if existing is not None:
        print(
            f"user {args.user_id!r} already has an ACTIVE profile "
            f"({existing['profile_version']}). This script only bootstraps the FIRST "
            "model. Use the reviewed Model Update Manager (G1-G6 promotion, "
            "UpdateManager.run_scheduled) for subsequent updates."
        )
        return 1

    windows = _load_windows(storage, args.user_id)
    if not windows:
        print(f"no feature_windows found for user {args.user_id!r}; nothing to train on.")
        return 1

    ml_config = load_ml_config(args.ml_config)
    artifacts = train_user_profile(args.user_id, windows, ml_config)
    if not artifacts:
        print(
            f"neither modality had enough eligible windows "
            f"(need >= min_baseline_windows) out of {len(windows)} collected windows."
        )
        return 1

    profile_version = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%f")
    keyboard_artifact = artifacts.get("keyboard")
    mouse_artifact = artifacts.get("mouse")

    if keyboard_artifact is not None:
        save_artifact(
            keyboard_artifact,
            args.artifact_root / args.user_id / f"{keyboard_artifact.version}.joblib",
        )
    if mouse_artifact is not None:
        save_artifact(
            mouse_artifact,
            args.artifact_root / args.user_id / f"{mouse_artifact.version}.joblib",
        )

    aggregate_checksum = _aggregate_checksum(
        profile_version,
        keyboard_artifact.version if keyboard_artifact else None,
        keyboard_artifact.checksum if keyboard_artifact else None,
        mouse_artifact.version if mouse_artifact else None,
        mouse_artifact.checksum if mouse_artifact else None,
    )

    placeholder_metrics = ValidationMetrics(false_rejection_rate=0.0, false_acceptance_rate=0.0)
    profile = ModelProfile(
        profile_version=profile_version,
        user_id=args.user_id,
        keyboard_artifact_version=keyboard_artifact.version if keyboard_artifact else None,
        mouse_artifact_version=mouse_artifact.version if mouse_artifact else None,
        aggregate_checksum=aggregate_checksum,
        validation=ValidationReport(
            accepted=True,
            code="BOOTSTRAP_INITIAL_ENROLLMENT_NO_BASELINE",
            baseline=placeholder_metrics,
            candidate=placeholder_metrics,
        ),
        created_at=datetime.now(UTC),
    )

    repository = SQLiteUpdateRepository(storage)
    repository.activate_profile(profile, retained_versions=3)

    print(f"activated profile {profile_version} for user {args.user_id!r}:")
    if keyboard_artifact is not None:
        print(
            f"  keyboard: {keyboard_artifact.version} "
            f"(trained on {keyboard_artifact.metrics_at_training['n_training_windows']} windows)"
        )
    else:
        print("  keyboard: unavailable (too few eligible windows)")
    if mouse_artifact is not None:
        print(
            f"  mouse:    {mouse_artifact.version} "
            f"(trained on {mouse_artifact.metrics_at_training['n_training_windows']} windows)"
        )
    else:
        print("  mouse:    unavailable (too few eligible windows)")
    print(
        "\nThis is a single-day, no-holdout bootstrap model for local testing only. "
        "It proves the pipeline is mechanically wired end to end; it is not a claim "
        "of real-world authentication accuracy (see docs/evaluation.md)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
