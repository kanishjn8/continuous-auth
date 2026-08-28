"""Create host-local storage locations with access restricted to this account."""

from __future__ import annotations

import getpass
import os
import subprocess
from pathlib import Path

from .errors import StoragePermissionError


def restrict_directory(path: Path) -> None:
    """Create and restrict a directory, failing rather than silently weakening it."""

    try:
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            account = getpass.getuser()
            result = subprocess.run(
                [
                    "icacls",
                    str(path),
                    "/inheritance:r",
                    "/grant:r",
                    f"{account}:(OI)(CI)F",
                    "*S-1-5-18:(OI)(CI)F",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip()
                raise StoragePermissionError(f"could not restrict storage directory: {detail}")
        else:
            path.chmod(0o700)
    except StoragePermissionError:
        raise
    except OSError as exc:
        raise StoragePermissionError(
            f"could not create or restrict storage directory: {exc.strerror or exc}"
        ) from exc


def restrict_file(path: Path) -> None:
    """Restrict a created file on POSIX; Windows inherits the private directory ACL."""

    if os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError as exc:
            raise StoragePermissionError(
                f"could not restrict storage file: {exc.strerror or exc}"
            ) from exc
