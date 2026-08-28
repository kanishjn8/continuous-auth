"""Strict configuration loader for T-003 synthetic scenarios."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from protocol.generated.python.contracts import (
    ApplicationCategory,
    InputDeviceClass,
    KeyClass,
    RiskLevel,
)

DEFAULT_SYNTHETIC_CONFIG = (
    Path(__file__).resolve().parents[2] / "config" / "synthetic.development.yaml"
)


class SyntheticConfigError(ValueError):
    """Raised when a synthetic scenario configuration cannot be used safely."""


class BehavioralProfile(BaseModel):
    """A synthetic identity pattern; values are not fitted to participant data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    keyboard_device: InputDeviceClass
    mouse_device: InputDeviceClass
    key_class_weights: dict[KeyClass, Annotated[float, Field(ge=0)]]
    mean_dwell_us: Annotated[float, Field(gt=0)]
    std_dwell_us: Annotated[float, Field(ge=0)]
    mean_key_interval_us: Annotated[float, Field(gt=0)]
    std_key_interval_us: Annotated[float, Field(ge=0)]
    mouse_step_us: Annotated[int, Field(gt=0)]
    mouse_distance_mean_px: Annotated[float, Field(gt=0)]
    mouse_distance_std_px: Annotated[float, Field(ge=0)]
    mouse_click_probability: Annotated[float, Field(ge=0, le=1)]
    mouse_scroll_probability: Annotated[float, Field(ge=0, le=1)]
    context_categories: Annotated[list[ApplicationCategory], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_profile(self) -> BehavioralProfile:
        if not self.key_class_weights or sum(self.key_class_weights.values()) <= 0:
            raise ValueError("key_class_weights must contain a positive total weight")
        if self.mouse_click_probability + self.mouse_scroll_probability > 1:
            raise ValueError("mouse click and scroll probabilities must sum to at most 1")
        return self


class ScorePhase(BaseModel):
    """Risk-oriented synthetic scores and the expected downstream risk class."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, Field(min_length=1)]
    start_fraction: Annotated[float, Field(ge=0, lt=1)]
    end_fraction: Annotated[float, Field(gt=0, le=1)]
    keyboard_risk_score: Annotated[float, Field(ge=0, le=1)]
    mouse_risk_score: Annotated[float, Field(ge=0, le=1)]
    expected_risk_level: RiskLevel

    @model_validator(mode="after")
    def validate_range(self) -> ScorePhase:
        if self.end_fraction <= self.start_fraction:
            raise ValueError("score phase end_fraction must be greater than start_fraction")
        return self


class FaultConfig(BaseModel):
    """Indices and payload used to build deterministic transport fault streams."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    gap_event_index: Annotated[int, Field(ge=0)]
    duplicate_event_index: Annotated[int, Field(ge=0)]
    out_of_order_event_index: Annotated[int, Field(ge=0)]
    malformed_payload: Annotated[str, Field(min_length=1)]


class ScenarioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seed: Annotated[int, Field(ge=0)]
    user_id: Annotated[str, Field(min_length=1)]
    session_id: Annotated[str, Field(min_length=1)]
    segment_id: Annotated[str, Field(min_length=1)]
    collection_day: str
    t_start_us: Annotated[int, Field(ge=0)]
    duration_us: Annotated[int, Field(gt=0)]
    app_id: Annotated[int, Field(ge=0)]
    profile: Annotated[str, Field(min_length=1)]
    takeover_profile: str | None
    takeover_fraction: Annotated[float, Field(gt=0, lt=1)] | None
    keyboard_enabled: bool
    mouse_enabled: bool
    context_enabled: bool
    heartbeat_enabled: bool
    context_switch_interval_us: Annotated[int, Field(gt=0)]
    heartbeat_interval_us: Annotated[int, Field(gt=0)]
    initial_mouse_x: int
    initial_mouse_y: int
    score_phases: Annotated[list[ScorePhase], Field(min_length=1)]
    faults: FaultConfig

    @model_validator(mode="after")
    def validate_scenario(self) -> ScenarioConfig:
        if (self.takeover_profile is None) != (self.takeover_fraction is None):
            raise ValueError("takeover_profile and takeover_fraction must be supplied together")
        if not any((self.keyboard_enabled, self.mouse_enabled, self.context_enabled)):
            raise ValueError("scenario must enable at least one event modality")
        expected_start = 0.0
        for phase in self.score_phases:
            if phase.start_fraction != expected_start:
                raise ValueError("score_phases must be contiguous and begin at 0")
            expected_start = phase.end_fraction
        if expected_start != 1.0:
            raise ValueError("score_phases must end at 1")
        return self


class SamplingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_interval_us: Annotated[float, Field(gt=0)]
    minimum_distance_px: Annotated[float, Field(gt=0)]
    max_attempts: Annotated[int, Field(gt=0)]


class SyntheticConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal[1]
    max_frame_bytes: Annotated[int, Field(gt=0)]
    sampling: SamplingConfig
    profiles: dict[str, BehavioralProfile]
    scenarios: dict[str, ScenarioConfig]

    @model_validator(mode="after")
    def validate_references(self) -> SyntheticConfig:
        if not self.profiles:
            raise ValueError("at least one behavioral profile is required")
        if not self.scenarios:
            raise ValueError("at least one scenario is required")
        for name, scenario in self.scenarios.items():
            if scenario.profile not in self.profiles:
                raise ValueError(
                    f"scenario {name!r} references unknown profile {scenario.profile!r}"
                )
            if (
                scenario.takeover_profile is not None
                and scenario.takeover_profile not in self.profiles
            ):
                raise ValueError(
                    f"scenario {name!r} references unknown takeover profile "
                    f"{scenario.takeover_profile!r}"
                )
        return self


def load_synthetic_config(path: Path | str | None = None) -> SyntheticConfig:
    """Load the whole file and reject missing, malformed, or extra values."""

    config_path = Path(path) if path is not None else DEFAULT_SYNTHETIC_CONFIG
    try:
        with config_path.open(encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise SyntheticConfigError(f"cannot load synthetic config {config_path}: {exc}") from exc

    try:
        return SyntheticConfig.model_validate(document)
    except ValueError as exc:
        raise SyntheticConfigError(f"invalid synthetic config {config_path}: {exc}") from exc
