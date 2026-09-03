"""Run the complete Windows synthetic-development stack."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from .application import create_collection_application

ROOT = Path(__file__).resolve().parents[3]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Continuous-authentication local runtime")
    parser.add_argument(
        "--participant-id",
        "--synthetic-user",
        dest="participant_id",
        required=True,
        help="Pseudonym this run collects under; keep it identical across every run",
    )
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--dashboard-directory", type=Path)
    parser.add_argument(
        "--storage-config",
        type=Path,
        default=ROOT / "config/storage.pilot.yaml",
        help=(
            "Storage profile, which also determines provenance. The default "
            "approved-collection profile records real PILOT participant data; pass "
            "config/storage.development.yaml to record disposable SYNTHETIC data instead."
        ),
    )
    parser.add_argument(
        "--collector-config", type=Path, default=ROOT / "config/collector.development.yaml"
    )
    parser.add_argument(
        "--ingestion-config", type=Path, default=ROOT / "config/ingestion.development.yaml"
    )
    parser.add_argument("--ml-config", type=Path, default=ROOT / "config/ml.development.yaml")
    parser.add_argument("--risk-config", type=Path, default=ROOT / "config/risk.development.yaml")
    parser.add_argument(
        "--context-config", type=Path, default=ROOT / "config/context.development.yaml"
    )
    parser.add_argument("--api-config", type=Path, default=ROOT / "config/api.development.yaml")
    parser.add_argument(
        "--updates-config", type=Path, default=ROOT / "config/updates.development.yaml"
    )
    parser.add_argument(
        "--orchestration-config",
        type=Path,
        default=ROOT / "config/orchestration.development.yaml",
    )
    parser.add_argument(
        "--enforcement-config",
        type=Path,
        default=ROOT / "config/enforcement.development.yaml",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    local_secret = os.environ.get("CA_DASHBOARD_SECRET")
    if local_secret is None or not local_secret:
        raise SystemExit("CA_DASHBOARD_SECRET must be set")
    integrated = create_collection_application(
        local_secret=local_secret,
        participant_id=arguments.participant_id,
        workspace_root=ROOT,
        artifact_root=arguments.artifact_root,
        storage_config=arguments.storage_config,
        collector_config=arguments.collector_config,
        ingestion_config=arguments.ingestion_config,
        ml_config=arguments.ml_config,
        risk_config=arguments.risk_config,
        context_config=arguments.context_config,
        api_config=arguments.api_config,
        updates_config=arguments.updates_config,
        orchestration_config=arguments.orchestration_config,
        enforcement_config=arguments.enforcement_config,
        dashboard_directory=arguments.dashboard_directory,
    )
    uvicorn.run(
        integrated.app,
        host=integrated.api_settings.api.bind_host,
        port=integrated.api_settings.api.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
