"""Strict C9 loader for local REST/WebSocket settings."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from protocol.generated.python.contracts import ApiConfig

DEFAULT_API_CONFIG = Path(__file__).resolve().parents[3] / "config" / "api.development.yaml"


class ApiConfigError(ValueError):
    pass


class _ApiRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config_version: Annotated[str, Field(min_length=1)]
    protocol_version: Literal["1.0.0"]
    development_only: Literal[True]
    api: ApiConfig


@dataclass(frozen=True)
class ApiSettings:
    config_version: str
    protocol_version: str
    config_checksum: str
    api: ApiConfig


def load_api_settings(path: Path | str | None = None) -> ApiSettings:
    config_path = Path(path) if path is not None else DEFAULT_API_CONFIG
    try:
        document: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        model = _ApiRuntimeConfig.model_validate(document)
    except OSError as exc:
        raise ApiConfigError(f"cannot read API config: {exc.strerror or exc}") from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise ApiConfigError(f"API config failed validation: {exc}") from exc
    canonical = json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return ApiSettings(
        config_version=model.config_version,
        protocol_version=model.protocol_version,
        config_checksum=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        api=model.api,
    )
