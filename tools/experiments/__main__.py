"""CLI entry point: ``python -m tools.experiments e1|e2``.

Runs the E1 drift-benefit or E2 poisoning-resistance experiment (PLAN.md
Section 12.4) against a frozen PILOT corpus. See ``tools/experiments/drift.py``
and ``tools/experiments/poisoning.py`` for what these results do and do not
prove.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.risk.config import load_risk_settings
from backend.app.storage.config import default_storage_config, load_storage_settings
from backend.app.storage.service import StorageService
from backend.app.updates.config import load_update_settings
from ml.features.config import load_config as load_ml_config
from tools.collection.config import load_collection_settings

from .drift import run_drift_benefit
from .poisoning import run_poisoning_resistance

ROOT = Path(__file__).resolve().parents[2]


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--impostor-id", required=True)
    parser.add_argument("--injected-segment-id", required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--administration", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--storage-config", type=Path, default=default_storage_config(ROOT))
    parser.add_argument(
        "--collection-config", type=Path, default=ROOT / "config/collection.pilot.yaml"
    )
    # No default: neither an approved config/ml.pilot.yaml nor
    # config/risk.pilot.yaml exists yet (see tools/enrollment/activate.py).
    parser.add_argument("--ml-config", type=Path, required=True)
    parser.add_argument("--risk-config", type=Path, required=True)
    parser.add_argument("--update-config", type=Path, default=None)
    parser.add_argument("--evaluation-freeze", type=Path, required=True)
    parser.add_argument("--evaluation-config-directory", type=Path, required=True)
    parser.add_argument("--code-revision", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run E1 (drift-benefit) or E2 (poisoning-resistance) over the frozen PILOT corpus"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    e1 = subparsers.add_parser("e1", help="Drift-benefit experiment (frozen vs. updated profile)")
    _common_arguments(e1)
    e1.add_argument("--early-train-days", type=int, default=None)

    e2 = subparsers.add_parser("e2", help="Poisoning-resistance experiment")
    _common_arguments(e2)

    return parser


def _load_common(args: argparse.Namespace) -> dict:
    storage_settings = load_storage_settings(args.storage_config, workspace_root=ROOT)
    storage = StorageService.open(storage_settings)
    return {
        "user_id": args.user_id,
        "impostor_id": args.impostor_id,
        "injected_segment_id": args.injected_segment_id,
        "database": args.database,
        "manifest": args.manifest,
        "administration": args.administration,
        "storage": storage,
        "artifact_root": args.artifact_root,
        "ml_config": load_ml_config(args.ml_config),
        "collection_settings": load_collection_settings(args.collection_config),
        "risk_settings": load_risk_settings(args.risk_config),
        "evaluation_freeze": args.evaluation_freeze,
        "config_directory": args.evaluation_config_directory,
        "dataset_manifest": args.manifest,
        "code_revision": args.code_revision,
        "update_settings": (
            load_update_settings(args.update_config)
            if args.update_config is not None
            else load_update_settings()
        ),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    context = _load_common(args)

    if args.command == "e1":
        result = run_drift_benefit(**context, early_train_days=args.early_train_days)
        payload = {
            "experiment": "E1",
            "frozen_frr": result.frozen.false_rejection_rate,
            "frozen_far": result.frozen.false_acceptance_rate,
            "updated_frr": result.updated.false_rejection_rate,
            "updated_far": result.updated.false_acceptance_rate,
            "false_rejection_change": result.false_rejection_change,
            "false_acceptance_change": result.false_acceptance_change,
            "false_rejection_change_ci": list(result.false_rejection_change_ci),
        }
    else:
        result = run_poisoning_resistance(**context)
        payload = {
            "experiment": "E2",
            "injected_segments": result.injected_segments,
            "injected_rejected_or_invalidated": result.injected_rejected_or_invalidated,
            "injected_promoted": result.injected_promoted,
            "clean_segments": result.clean_segments,
            "clean_promoted": result.clean_promoted,
            "poisoning_block_rate": result.poisoning_block_rate,
        }

    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
