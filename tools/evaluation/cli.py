"""Create or verify an immutable final-evaluation lock."""

from __future__ import annotations

import argparse
from pathlib import Path

from .freeze import create_evaluation_freeze, verify_evaluation_freeze


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--config", type=Path, action="append", required=True)
    create.add_argument("--dataset-manifest", type=Path, required=True)
    create.add_argument("--revision", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--freeze", type=Path, required=True)
    verify.add_argument("--config-directory", type=Path, required=True)
    verify.add_argument("--dataset-manifest", type=Path, required=True)
    verify.add_argument("--revision", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "create":
        create_evaluation_freeze(
            arguments.output,
            config_paths=arguments.config,
            dataset_manifest=arguments.dataset_manifest,
            code_revision=arguments.revision,
        )
        print("Evaluation freeze created.")
        return 0
    verify_evaluation_freeze(
        arguments.freeze,
        config_directory=arguments.config_directory,
        dataset_manifest=arguments.dataset_manifest,
        code_revision=arguments.revision,
    )
    print("Evaluation freeze verified.")
    return 0
