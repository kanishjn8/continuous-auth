"""Read the authoritative collector transport settings needed by the backend."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from protocol.generated.python.contracts import CollectorConfig


class CollectorRuntimeConfigError(ValueError):
    pass


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config_version: Annotated[str, Field(min_length=1)]
    protocol_version: Literal["1.0.0"]
    collector: CollectorConfig


def load_collector_config(path: Path) -> CollectorConfig:
    try:
        document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        return _Document.model_validate(document).collector
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise CollectorRuntimeConfigError(f"collector config rejected: {exc}") from exc
