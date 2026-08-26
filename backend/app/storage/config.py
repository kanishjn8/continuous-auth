"""Load and validate the C9 storage configuration without hidden defaults."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from protocol.generated.python.contracts import (
    RetentionConfig,
    StorageDataPolicy,
    StorageEnvironment,
    StorageRuntimeConfig,
)

from .errors import StorageConfigError

_ENVIRONMENT_REFERENCE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
_CLOUD_DIRECTORY_NAMES = frozenset(
    {"onedrive", "dropbox", "google drive", "icloud", "icloud drive"}
)


@dataclass(frozen=True)
class StorageSettings:
    """Resolved, validated settings consumed by the storage implementation."""

    config_version: str
    protocol_version: str
    environment: StorageEnvironment
    data_policy: StorageDataPolicy
    root_directory: Path
    database_path: Path
    audit_directory: Path
    busy_timeout_ms: int
    retention: RetentionConfig

    @property
    def synthetic_only(self) -> bool:
        return self.data_policy == StorageDataPolicy.SYNTHETIC_ONLY


def _substitute_environment(text: str, environment: dict[str, str]) -> str:
    missing: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = environment.get(name)
        if value is None or not value.strip():
            missing.add(name)
            return match.group(0)
        return value.replace("\\", "/")

    rendered = _ENVIRONMENT_REFERENCE.sub(replace, text)
    if missing:
        names = ", ".join(sorted(missing))
        raise StorageConfigError(f"storage config is missing environment variable(s): {names}")
    return rendered


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_location(root: Path, workspace_root: Path | None) -> None:
    if not root.is_absolute():
        raise StorageConfigError("storage root_directory must be an absolute local path")
    if any(part.casefold() in _CLOUD_DIRECTORY_NAMES for part in root.parts):
        raise StorageConfigError(
            "storage root_directory cannot be inside a cloud-synchronised folder"
        )
    if workspace_root is not None and _inside(root, workspace_root.resolve()):
        raise StorageConfigError("storage root_directory must be outside the source workspace")


def _validate_cross_field_rules(model: StorageRuntimeConfig) -> None:
    storage = model.storage
    retention = model.retention
    is_pilot = storage.environment == StorageEnvironment.PILOT
    if retention.pilot_mode != is_pilot:
        raise StorageConfigError("retention.pilot_mode must match storage.environment PILOT")
    if retention.audit_delete_after_days < retention.audit_compress_after_days:
        raise StorageConfigError(
            "audit_delete_after_days cannot be shorter than audit_compress_after_days"
        )


def load_storage_settings(
    config_path: Path,
    *,
    workspace_root: Path | None = None,
    environment: dict[str, str] | None = None,
) -> StorageSettings:
    """Load a YAML file, resolve declared variables, and enforce C9."""

    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StorageConfigError(f"cannot read storage config: {exc.strerror or exc}") from exc

    try:
        source_environment = dict(os.environ) if environment is None else environment
        rendered = _substitute_environment(text, source_environment)
        document: Any = yaml.safe_load(rendered)
        if not isinstance(document, dict):
            raise StorageConfigError("storage config must contain a YAML object")
        model = StorageRuntimeConfig.model_validate(document)
    except (yaml.YAMLError, ValidationError) as exc:
        raise StorageConfigError(f"storage config failed C9 validation: {exc}") from exc

    _validate_cross_field_rules(model)
    root = Path(model.storage.root_directory).expanduser().resolve()
    _validate_location(root, workspace_root)
    return StorageSettings(
        config_version=model.config_version,
        protocol_version=model.protocol_version,
        environment=model.storage.environment,
        data_policy=model.storage.data_policy,
        root_directory=root,
        database_path=root / model.storage.database_filename,
        audit_directory=root / model.storage.audit_directory_name,
        busy_timeout_ms=model.storage.busy_timeout_ms,
        retention=model.retention,
    )
