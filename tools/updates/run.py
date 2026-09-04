"""Drive a scheduled Model Update Manager run over the frozen PILOT corpus.

``UpdateManager.run_scheduled`` (backend/app/updates/manager.py) has existed
since T-018 with no production caller: only tests supplied it a
``candidate_builder``. This module is that caller. It is what makes the
G1-G6 promotion gate operative on real collection data rather than latent
behind a unit test.

``config/updates.development.yaml`` remains the update configuration --
there is deliberately no ``config/updates.pilot.yaml`` (see
``tools/enrollment/activate.py`` for the identical reasoning): every loader
in ``backend/app/updates/config.py`` requires ``development_only: true``,
which a real pilot policy file could not honestly claim, and creating one
that lied about that flag would be worse than reusing the existing file.
Its ``quarantine_days: 7`` and ``retraining_cadence_days: 7`` are UNCHANGED
by this module and must stay that way (Section 6.3 of the pilot default
workflow design): reducing them so a five-day collection round could
produce a promotion would weaken a guardrail purely to make the pipeline
run on a schedule that does not honestly support it.

Consequence, spelled out because it is easy to mistake for a bug: a
five-day collection round produces **no live promotion**. Every segment
quarantined during days 1-5 clears G5 only on quarantined_at + 7 days, i.e.
day 8 at the earliest -- after the round has already ended. Running this
module against a five-day round's data therefore returns
``REJECTED`` / ``NO_ELIGIBLE_CANDIDATES``, honestly, every time. The pilot
collects; the Model Update Manager acts afterwards, once quarantine has
genuinely elapsed and a retraining cadence is genuinely due.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from backend.app.runtime.profiles import DirectoryProfileProvider
from backend.app.storage.config import load_storage_settings
from backend.app.storage.service import StorageService
from backend.app.updates.config import UpdateSettings, load_update_settings
from backend.app.updates.manager import (
    CandidateBuild,
    UpdateManager,
    UpdateRunOutcome,
    ValidationMetrics,
)
from backend.app.updates.repository import SQLiteUpdateRepository
from ml.evaluation.cross_evaluation import zero_effort_cross_evaluation
from ml.evaluation.metrics import compute_eer, compute_far_frr
from ml.features.config import MLConfig
from ml.features.config import load_config as load_ml_config
from ml.training import isolation_forest
from ml.training.common import ModelArtifact
from ml.training.persistence import save_artifact
from protocol.generated.python.contracts import UpdateCandidate
from tools.collection import corpus as corpus_module

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class UpdateRunContext:
    """Everything ``build_candidate`` needs, bound once per ``run_update`` call."""

    database: Path
    manifest: Path
    storage: StorageService
    artifact_root: Path
    ml_config: MLConfig


def _aggregate_checksum(
    profile_version: str,
    keyboard_artifact: ModelArtifact | None,
    mouse_artifact: ModelArtifact | None,
) -> str:
    import hashlib

    values = (
        profile_version,
        keyboard_artifact.version if keyboard_artifact else "",
        keyboard_artifact.checksum if keyboard_artifact else "",
        mouse_artifact.version if mouse_artifact else "",
        mouse_artifact.checksum if mouse_artifact else "",
    )
    return hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()


def _measure_validation_metrics(
    user_id: str,
    profile: dict[str, ModelArtifact],
    validation_windows_by_user: dict[str, list],
) -> ValidationMetrics:
    """Measure real FRR/FAR on VALIDATION: never fabricated, never guessed.

    Identical measurement convention to
    ``tools/enrollment/activate.py::_measure_validation_metrics``: FRR from
    the user's own held-out VALIDATION windows, FAR from zero-effort
    cross-evaluation against every other participant's VALIDATION windows,
    pooled across whichever modalities trained, with the operating
    threshold at the pooled Equal Error Rate. This is called once for the
    currently active (baseline) profile and once for the freshly trained
    candidate, both against the same VALIDATION windows, so the comparison
    the Model Update Manager regresses on is apples-to-apples.
    """

    import numpy as np

    genuine: list[float] = []
    impostor: list[float] = []
    for artifact in profile.values():
        cross_results = zero_effort_cross_evaluation({user_id: artifact}, validation_windows_by_user)
        result = cross_results.get(user_id)
        if result is None:
            continue
        genuine.extend(result.genuine_scores)
        impostor.extend(result.all_impostor_scores().tolist())

    if not genuine or not impostor:
        raise ValueError(
            "UPDATE_VALIDATION_INSUFFICIENT: no genuine and/or impostor VALIDATION-"
            f"partition scores were available for user {user_id!r}; a scheduled "
            "update cannot be validated without a real measurement"
        )

    genuine_arr = np.asarray(genuine, dtype=float)
    impostor_arr = np.asarray(impostor, dtype=float)
    eer = compute_eer(genuine_arr, impostor_arr)
    rates = compute_far_frr(genuine_arr, impostor_arr, eer.threshold)
    return ValidationMetrics(
        false_rejection_rate=rates.frr,
        false_acceptance_rate=rates.far,
    )


def build_candidate(
    user_id: str,
    promoted: tuple[UpdateCandidate, ...],
    *,
    context: UpdateRunContext,
) -> CandidateBuild:
    """Train, save, and validate a candidate profile from promoted segments only.

    1. Load the promoted candidates' segments' windows from the TRAIN
       partition of the frozen corpus -- never the whole user history, only
       what actually cleared G1-G6.
    2. Train through ``ml.training.isolation_forest`` (accessed via the
       module, not a direct import, so callers that intercept it -- this
       module's own tests included -- can observe exactly what training
       received) with ``promoted_candidates`` set, so
       ``require_promotion_gate`` is exercised for real.
    3. Save the resulting artifacts and measure ``baseline_metrics`` from
       the currently ACTIVE profile and ``candidate_metrics`` from the new
       one, both on the VALIDATION partition with cross-user impostors.
    """

    train_corpus = corpus_module.load_frozen_corpus(context.database, context.manifest, "TRAIN")
    validation_corpus = corpus_module.load_frozen_corpus(
        context.database, context.manifest, "VALIDATION"
    )

    promoted_by_segment = {candidate.segment_id: candidate for candidate in promoted}
    segment_ids = set(promoted_by_segment)
    train_windows = [
        window
        for window in train_corpus.windows_by_user.get(user_id, [])
        if window.segment_id in segment_ids
    ]
    if not train_windows:
        raise ValueError(
            f"no TRAIN-partition windows found for promoted segments {sorted(segment_ids)!r} "
            f"of user {user_id!r}"
        )

    candidate_profile = isolation_forest.train_user_profile(
        user_id,
        train_windows,
        context.ml_config,
        promoted_candidates=promoted_by_segment,
    )
    if not candidate_profile:
        raise ValueError(
            f"neither modality trained a candidate model for user {user_id!r}: "
            f"{len(train_windows)} promoted TRAIN windows did not clear "
            "min_baseline_windows for either keyboard or mouse"
        )

    profile_version = datetime.now().strftime("%Y%m%dT%H%M%S.%f")
    keyboard_artifact = candidate_profile.get("keyboard")
    mouse_artifact = candidate_profile.get("mouse")

    if keyboard_artifact is not None:
        save_artifact(
            keyboard_artifact,
            context.artifact_root / user_id / f"{keyboard_artifact.version}.joblib",
        )
    if mouse_artifact is not None:
        save_artifact(
            mouse_artifact,
            context.artifact_root / user_id / f"{mouse_artifact.version}.joblib",
        )

    active_provider = DirectoryProfileProvider(context.storage, context.artifact_root)
    active = active_provider(user_id)
    if active is None:
        raise ValueError(
            f"user {user_id!r} has no ACTIVE profile to validate a scheduled update "
            "against; a first profile is created through enrollment admission "
            "(tools/enrollment/activate.py), not a scheduled update"
        )
    baseline_artifacts = {
        modality: artifact
        for modality, artifact in (("keyboard", active.keyboard), ("mouse", active.mouse))
        if artifact is not None
    }

    baseline_metrics = _measure_validation_metrics(
        user_id, baseline_artifacts, validation_corpus.windows_by_user
    )
    candidate_metrics = _measure_validation_metrics(
        user_id, candidate_profile, validation_corpus.windows_by_user
    )

    return CandidateBuild(
        profile_version=profile_version,
        keyboard_artifact_version=keyboard_artifact.version if keyboard_artifact else None,
        keyboard_checksum=keyboard_artifact.checksum if keyboard_artifact else None,
        mouse_artifact_version=mouse_artifact.version if mouse_artifact else None,
        mouse_checksum=mouse_artifact.checksum if mouse_artifact else None,
        baseline_metrics=baseline_metrics,
        candidate_metrics=candidate_metrics,
    )


def run_update(
    *,
    user_id: str,
    database: Path,
    manifest: Path,
    storage: StorageService,
    artifact_root: Path,
    ml_config: MLConfig,
    scheduled_for: datetime,
    update_settings: UpdateSettings | None = None,
) -> UpdateRunOutcome:
    """Load settings, build an ``UpdateManager``, and run one scheduled update.

    ``update_settings`` defaults to ``config/updates.development.yaml`` (see
    the module docstring for why no pilot variant exists), keeping
    ``quarantine_days`` and ``retraining_cadence_days`` at 7.
    """

    settings = update_settings if update_settings is not None else load_update_settings()
    repository = SQLiteUpdateRepository(storage)
    manager = UpdateManager(settings, repository)
    context = UpdateRunContext(
        database=database,
        manifest=manifest,
        storage=storage,
        artifact_root=artifact_root,
        ml_config=ml_config,
    )

    def candidate_builder(
        candidate_user_id: str, promoted: tuple[UpdateCandidate, ...]
    ) -> CandidateBuild:
        return build_candidate(candidate_user_id, promoted, context=context)

    return manager.run_scheduled(
        user_id=user_id, scheduled_for=scheduled_for, candidate_builder=candidate_builder
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one scheduled Model Update Manager cycle over the frozen PILOT corpus"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--user-id", required=True)
    run.add_argument("--database", type=Path, required=True)
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--artifact-root", type=Path, required=True)
    run.add_argument(
        "--storage-config", type=Path, default=ROOT / "config/storage.pilot.yaml"
    )
    # No default: see tools/enrollment/activate.py -- there is no approved
    # config/ml.pilot.yaml in this repository, so the operator must name one.
    run.add_argument("--ml-config", type=Path, required=True)
    run.add_argument("--update-config", type=Path, default=None)
    run.add_argument(
        "--scheduled-for",
        required=True,
        type=lambda value: _parse_scheduled_for(value),
        help="ISO-8601 timezone-aware timestamp, e.g. 2026-09-12T00:00:00Z",
    )
    return parser


def _parse_scheduled_for(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--scheduled-for must be timezone-aware ISO-8601")
    return parsed


def main(argv: list[str] | None = None) -> int:
    import json

    args = _parser().parse_args(argv)

    storage_settings = load_storage_settings(args.storage_config, workspace_root=ROOT)
    storage = StorageService.open(storage_settings)
    ml_config = load_ml_config(args.ml_config)
    update_settings = (
        load_update_settings(args.update_config)
        if args.update_config is not None
        else load_update_settings()
    )

    outcome = run_update(
        user_id=args.user_id,
        database=args.database,
        manifest=args.manifest,
        storage=storage,
        artifact_root=args.artifact_root,
        ml_config=ml_config,
        scheduled_for=args.scheduled_for,
        update_settings=update_settings,
    )

    print(
        json.dumps(
            {
                "run_id": outcome.run_id,
                "status": outcome.status,
                "code": outcome.code,
                "profile_version": (
                    outcome.profile.profile_version if outcome.profile is not None else None
                ),
            },
            sort_keys=True,
        )
    )
    return 0 if outcome.status in {"ACTIVATED", "SKIPPED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
