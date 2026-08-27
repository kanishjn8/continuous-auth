"""Strict local orchestration configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_ORCHESTRATION_CONFIG = (
    Path(__file__).resolve().parents[3] / "config" / "orchestration.development.yaml"
)


class OrchestrationConfigError(ValueError):
    pass


class _Values(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pipe_read_bytes: Annotated[int, Field(ge=1024)]
    pipe_buffer_bytes: Annotated[int, Field(ge=4096)]
    heartbeat_timeout_seconds: Annotated[float, Field(gt=0)]
    watchdog_interval_seconds: Annotated[float, Field(gt=0)]
    measurement_capacity: Annotated[int, Field(ge=1)]


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config_version: Annotated[str, Field(min_length=1)]
    protocol_version: Literal["1.0.0"]
    development_only: Literal[True]
    orchestration: _Values


@dataclass(frozen=True)
class OrchestrationSettings:
    config_version: str
    pipe_read_bytes: int
    pipe_buffer_bytes: int
    heartbeat_timeout_seconds: float
    watchdog_interval_seconds: float
    measurement_capacity: int


def load_orchestration_settings(
    path: Path | str | None = None,
) -> OrchestrationSettings:
    source = Path(path) if path is not None else DEFAULT_ORCHESTRATION_CONFIG
    try:
        document: Any = yaml.safe_load(source.read_text(encoding="utf-8"))
        parsed = _Document.model_validate(document)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise OrchestrationConfigError(f"orchestration config rejected: {exc}") from exc
    values = parsed.orchestration
    if values.pipe_read_bytes > values.pipe_buffer_bytes:
        raise OrchestrationConfigError("pipe_read_bytes cannot exceed pipe_buffer_bytes")
    return OrchestrationSettings(
        config_version=parsed.config_version,
        pipe_read_bytes=values.pipe_read_bytes,
        pipe_buffer_bytes=values.pipe_buffer_bytes,
        heartbeat_timeout_seconds=values.heartbeat_timeout_seconds,
        watchdog_interval_seconds=values.watchdog_interval_seconds,
        measurement_capacity=values.measurement_capacity,
    )
