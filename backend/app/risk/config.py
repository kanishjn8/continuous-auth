"""Strict development configuration for the T-013 risk engine."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from protocol.generated.python.contracts import EnrollmentConfig, RiskConfig

DEFAULT_RISK_CONFIG = Path(__file__).resolve().parents[3] / "config" / "risk.development.yaml"


class RiskConfigError(ValueError):
    pass


class _RiskRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config_version: Annotated[str, Field(min_length=1)]
    protocol_version: Literal["1.0.0"]
    development_only: Literal[True]
    enrollment: EnrollmentConfig
    risk: RiskConfig


@dataclass(frozen=True)
class RiskSettings:
    config_version: str
    protocol_version: str
    config_checksum: str
    enrollment: EnrollmentConfig
    risk: RiskConfig

    @property
    def cooldown_us(self) -> int:
        return self.risk.cooldown_seconds * 1_000_000


def load_risk_settings(path: Path | str | None = None) -> RiskSettings:
    config_path = Path(path) if path is not None else DEFAULT_RISK_CONFIG
    try:
        document: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        model = _RiskRuntimeConfig.model_validate(document)
    except OSError as exc:
        raise RiskConfigError(f"cannot read risk config: {exc.strerror or exc}") from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise RiskConfigError(f"risk config failed validation: {exc}") from exc

    risk = model.risk
    if risk.keyboard_weight <= 0 or risk.mouse_weight <= 0:
        raise RiskConfigError("development modality weights must both be positive")
    if risk.medium_threshold >= risk.high_threshold:
        raise RiskConfigError("medium_threshold must be lower than high_threshold")
    if risk.breach_k > risk.breach_n:
        raise RiskConfigError("breach_k cannot exceed breach_n")
    canonical = json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return RiskSettings(
        config_version=model.config_version,
        protocol_version=model.protocol_version,
        config_checksum=checksum,
        enrollment=model.enrollment,
        risk=risk,
    )
