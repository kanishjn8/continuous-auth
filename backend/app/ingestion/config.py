"""Strict local configuration for T-007."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from protocol.generated.python.contracts import PROTOCOL_VERSION, IngestionConfig

from .errors import IngestionConfigError

DEFAULT_INGESTION_CONFIG = (
    Path(__file__).resolve().parents[3] / "config" / "ingestion.development.yaml"
)


class _IngestionRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config_version: Annotated[str, Field(min_length=1)]
    protocol_version: Literal["1.0.0"]
    ingestion: IngestionConfig


@dataclass(frozen=True)
class IngestionSettings:
    config_version: str
    protocol_version: str
    max_frame_bytes: int
    raw_event_ring_capacity: int
    idle_split_seconds: int

    @property
    def idle_split_us(self) -> int:
        return self.idle_split_seconds * 1_000_000


def load_ingestion_settings(path: Path | str | None = None) -> IngestionSettings:
    config_path = Path(path) if path is not None else DEFAULT_INGESTION_CONFIG
    try:
        document: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        model = _IngestionRuntimeConfig.model_validate(document)
    except OSError as exc:
        raise IngestionConfigError(
            f"cannot read ingestion config {config_path}: {exc.strerror or exc}"
        ) from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise IngestionConfigError(
            f"ingestion config {config_path} failed validation: {exc}"
        ) from exc

    if model.protocol_version != PROTOCOL_VERSION:
        raise IngestionConfigError(
            f"ingestion protocol {model.protocol_version!r} does not match {PROTOCOL_VERSION!r}"
        )
    return IngestionSettings(
        config_version=model.config_version,
        protocol_version=model.protocol_version,
        max_frame_bytes=model.ingestion.max_frame_bytes,
        raw_event_ring_capacity=model.ingestion.raw_event_ring_capacity,
        idle_split_seconds=model.ingestion.idle_split_seconds,
    )
