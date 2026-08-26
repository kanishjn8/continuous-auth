"""Small local operator commands for validating and initialising T-009."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_storage_settings
from .errors import StorageError
from .service import StorageService

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = WORKSPACE_ROOT / "config" / "storage.development.yaml"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("check", "init"),
        help="check validates only; init also creates the private local database",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        settings = load_storage_settings(
            args.config,
            workspace_root=WORKSPACE_ROOT,
        )
        if args.command == "check":
            print("T-009 storage configuration is valid (no data was created).")
            return 0
        StorageService.open(settings)
        print("T-009 private WAL database and audit directory are ready.")
        return 0
    except StorageError as exc:
        print(f"T-009 storage setup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
