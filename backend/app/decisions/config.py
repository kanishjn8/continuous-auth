"""Strict enforcement configuration for the native challenge path."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_ENFORCEMENT_CONFIG = (
    Path(__file__).resolve().parents[3] / "config" / "enforcement.development.yaml"
)


class EnforcementConfigError(ValueError):
    pass


class _Values(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    native_prompt: bool
    lock_workstation: bool
    challenge_timeout_seconds: Annotated[float, Field(gt=0)]
    answer_hash_iterations: Annotated[int, Field(ge=100000)]


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config_version: Annotated[str, Field(min_length=1)]
    protocol_version: Literal["1.0.0"]
    development_only: Literal[True]
    enforcement: _Values


@dataclass(frozen=True)
class EnforcementSettings:
    """Resolved enforcement policy; `enabled` gates every OS-level action."""

    config_version: str
    enabled: bool
    native_prompt: bool
    lock_workstation: bool
    challenge_timeout_seconds: float
    answer_hash_iterations: int

    @property
    def prompt_enabled(self) -> bool:
        return self.enabled and self.native_prompt

    @property
    def workstation_lock_enabled(self) -> bool:
        return self.enabled and self.lock_workstation


def load_enforcement_settings(path: Path | str | None = None) -> EnforcementSettings:
    source = Path(path) if path is not None else DEFAULT_ENFORCEMENT_CONFIG
    try:
        document: Any = yaml.safe_load(source.read_text(encoding="utf-8"))
        parsed = _Document.model_validate(document)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise EnforcementConfigError(f"enforcement config rejected: {exc}") from exc
    values = parsed.enforcement
    return EnforcementSettings(
        config_version=parsed.config_version,
        enabled=values.enabled,
        native_prompt=values.native_prompt,
        lock_workstation=values.lock_workstation,
        challenge_timeout_seconds=values.challenge_timeout_seconds,
        answer_hash_iterations=values.answer_hash_iterations,
    )
