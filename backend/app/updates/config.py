"""Validated T-018 configuration; open policy values stay external to code."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from protocol.generated.python.contracts import UpdateManagerConfig

DEFAULT_UPDATE_CONFIG = Path(__file__).resolve().parents[3] / "config" / "updates.development.yaml"


class UpdateConfigError(ValueError):
    pass


class _UpdateRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config_version: Annotated[str, Field(min_length=1)]
    protocol_version: Literal["1.0.0"]
    development_only: Literal[True]
    update_manager: UpdateManagerConfig


@dataclass(frozen=True)
class UpdateSettings:
    config_version: str
    protocol_version: str
    config_checksum: str
    update_manager: UpdateManagerConfig


def load_update_settings(path: Path | str | None = None) -> UpdateSettings:
    config_path = Path(path) if path is not None else DEFAULT_UPDATE_CONFIG
    try:
        document: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        model = _UpdateRuntimeConfig.model_validate(document)
    except OSError as exc:
        raise UpdateConfigError(f"cannot read update config: {exc.strerror or exc}") from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise UpdateConfigError(f"update config failed validation: {exc}") from exc
    canonical = json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return UpdateSettings(
        config_version=model.config_version,
        protocol_version=model.protocol_version,
        config_checksum=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        update_manager=model.update_manager,
    )
