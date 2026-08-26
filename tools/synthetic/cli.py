"""Command-line entry point for checking or writing T-003 scenarios."""

from __future__ import annotations

import argparse
from pathlib import Path

from ml.features.config import load_config as load_ml_config
from tools.synthetic.config import DEFAULT_SYNTHETIC_CONFIG, load_synthetic_config
from tools.synthetic.generator import generate_scenario
from tools.synthetic.output import write_bundle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_SYNTHETIC_CONFIG)
    parser.add_argument("--ml-config", type=Path)
    parser.add_argument("--scenario", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_synthetic_config(args.config)
    ml_config = load_ml_config(args.ml_config)
    bundle = generate_scenario(config, args.scenario, ml_config=ml_config)
    if args.output is not None:
        write_bundle(bundle, args.output)
        print(f"wrote validated synthetic scenario {args.scenario!r} to {args.output}")
    else:
        print(
            f"synthetic scenario {args.scenario!r} is valid: "
            f"{len(bundle.events)} events, {len(bundle.feature_windows)} windows"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
