"""Command-line collection health, freeze, verification, and pause operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_collection_settings
from .freeze import build_freeze, verify_freeze
from .health import build_health_report
from .pause import make_pause_controller
from .repository import load_administration_records, load_window_summaries


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggregate-only collection administration")
    parser.add_argument("--config", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("health", "freeze"):
        child = subparsers.add_parser(command)
        child.add_argument("--database", type=Path, required=True)
        child.add_argument("--administration", type=Path, required=True)
    freeze = subparsers.choices["freeze"]
    freeze.add_argument("--version", required=True)
    freeze.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--database", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    for command in ("pause", "resume"):
        child = subparsers.add_parser(command)
        child.add_argument("--event-name", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    settings = load_collection_settings(arguments.config)
    if arguments.command in {"pause", "resume"}:
        controller = make_pause_controller(arguments.event_name)
        controller.pause() if arguments.command == "pause" else controller.resume()
        return 0
    windows = load_window_summaries(arguments.database)
    if arguments.command == "verify":
        print(json.dumps(verify_freeze(arguments.manifest, windows), sort_keys=True))
        return 0
    consents, enrollments = load_administration_records(arguments.administration)
    if arguments.command == "health":
        report = build_health_report(
            windows, consents=consents, enrollments=enrollments, settings=settings
        )
        print(json.dumps(report.as_dict(), sort_keys=True, indent=2))
        return 0
    document = build_freeze(
        windows,
        destination=arguments.output,
        version=arguments.version,
        consents=consents,
        enrollments=enrollments,
        settings=settings,
    )
    print(json.dumps({"manifest_checksum": document["manifest_checksum"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
