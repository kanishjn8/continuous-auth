"""Strict configuration for collection health and dataset freezing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from protocol.generated.python.contracts import PROTOCOL_VERSION, DataProvenance


class CollectionConfigError(ValueError):
    """Collection configuration is missing or violates an operational boundary."""


class _CollectionValues(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target_collection_days: int = Field(ge=1)
    min_windows_per_day: int = Field(ge=1)
    min_full_modality_fraction: float = Field(ge=0, le=1)
    max_observed_gap_hours: float = Field(gt=0)
    scheduled_anchor_interval_hours: float = Field(gt=0)
    eligible_provenance: list[Annotated[DataProvenance, Field(strict=False)]] = Field(min_length=1)

    @model_validator(mode="after")
    def participant_provenance_only(self) -> _CollectionValues:
        forbidden = {DataProvenance.SYNTHETIC, DataProvenance.PUBLIC}
        if forbidden.intersection(self.eligible_provenance):
            raise ValueError("evaluation eligibility cannot include synthetic or public data")
        return self


class _FreezeValues(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    min_distinct_days: int = Field(ge=3)
    training_fraction: float = Field(gt=0, lt=1)
    validation_fraction: float = Field(gt=0, lt=1)
    evaluation_fraction: float = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def fractions_cover_corpus(self) -> _FreezeValues:
        total = self.training_fraction + self.validation_fraction + self.evaluation_fraction
        if abs(total - 1.0) > 1e-9:
            raise ValueError("freeze fractions must sum to 1.0")
        return self


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    config_version: str = Field(min_length=1)
    protocol_version: str
    collection: _CollectionValues
    freeze: _FreezeValues


@dataclass(frozen=True)
class CollectionSettings:
    config_version: str
    target_collection_days: int
    min_windows_per_day: int
    min_full_modality_fraction: float
    max_observed_gap_hours: float
    scheduled_anchor_interval_hours: float
    eligible_provenance: frozenset[DataProvenance]
    min_distinct_days: int
    training_fraction: float
    validation_fraction: float
    evaluation_fraction: float


def load_collection_settings(path: Path) -> CollectionSettings:
    try:
        document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        parsed = _Document.model_validate(document)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise CollectionConfigError(f"collection config rejected: {exc}") from exc
    if parsed.protocol_version != PROTOCOL_VERSION:
        raise CollectionConfigError("collection protocol version does not match the runtime")
    return CollectionSettings(
        config_version=parsed.config_version,
        target_collection_days=parsed.collection.target_collection_days,
        min_windows_per_day=parsed.collection.min_windows_per_day,
        min_full_modality_fraction=parsed.collection.min_full_modality_fraction,
        max_observed_gap_hours=parsed.collection.max_observed_gap_hours,
        scheduled_anchor_interval_hours=parsed.collection.scheduled_anchor_interval_hours,
        eligible_provenance=frozenset(parsed.collection.eligible_provenance),
        min_distinct_days=parsed.freeze.min_distinct_days,
        training_fraction=parsed.freeze.training_fraction,
        validation_fraction=parsed.freeze.validation_fraction,
        evaluation_fraction=parsed.freeze.evaluation_fraction,
    )
