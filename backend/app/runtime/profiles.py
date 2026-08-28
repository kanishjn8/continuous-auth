"""Resolve the active, schema-compatible per-user profile from retained local artifacts."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from backend.app.models.service import ProfileArtifacts
from backend.app.storage.service import StorageService
from ml.training.persistence import load_artifact

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]+$")


class ActiveProfileError(RuntimeError):
    pass


class DirectoryProfileProvider:
    """Load only the DB-designated active profile and verify its aggregate checksum."""

    def __init__(self, storage: StorageService, artifact_root: Path) -> None:
        self.storage = storage
        self.artifact_root = artifact_root
        self._cache: dict[tuple[str, str], ProfileArtifacts] = {}

    @staticmethod
    def _safe(value: str) -> str:
        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise ActiveProfileError("model identifier contains unsafe characters")
        return value

    def __call__(self, user_id: str) -> ProfileArtifacts | None:
        with self.storage.database.connection() as connection:
            row = connection.execute(
                """
                SELECT profile_version, keyboard_artifact_version,
                       mouse_artifact_version, aggregate_checksum
                FROM model_profiles WHERE user_id = ? AND status = 'ACTIVE'
                """,
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        profile_version = self._safe(str(row["profile_version"]))
        cache_key = (user_id, profile_version)
        if cache_key in self._cache:
            return self._cache[cache_key]
        safe_user = self._safe(user_id)
        keyboard_version = row["keyboard_artifact_version"]
        mouse_version = row["mouse_artifact_version"]
        keyboard = (
            None
            if keyboard_version is None
            else load_artifact(
                self.artifact_root / safe_user / f"{self._safe(str(keyboard_version))}.joblib"
            )
        )
        mouse = (
            None
            if mouse_version is None
            else load_artifact(
                self.artifact_root / safe_user / f"{self._safe(str(mouse_version))}.joblib"
            )
        )
        if keyboard is not None and keyboard.user_id != user_id:
            raise ActiveProfileError("keyboard artifact belongs to another user")
        if mouse is not None and mouse.user_id != user_id:
            raise ActiveProfileError("mouse artifact belongs to another user")
        values = (
            profile_version,
            "" if keyboard_version is None else str(keyboard_version),
            "" if keyboard is None else keyboard.checksum,
            "" if mouse_version is None else str(mouse_version),
            "" if mouse is None else mouse.checksum,
        )
        aggregate = hashlib.sha256("\x00".join(values).encode()).hexdigest()
        if aggregate != row["aggregate_checksum"]:
            raise ActiveProfileError("active profile aggregate checksum does not match")
        profile = ProfileArtifacts(
            user_id=user_id,
            profile_version=profile_version,
            model_version=profile_version,
            keyboard=keyboard,
            mouse=mouse,
        )
        self._cache = {key: value for key, value in self._cache.items() if key[0] != user_id}
        self._cache[cache_key] = profile
        return profile
