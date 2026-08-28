"""Strict development loader for T-012 context confidence."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from protocol.generated.python.contracts import ContextConfig

DEFAULT_CONTEXT_CONFIG = Path(__file__).resolve().parents[3] / "config" / "context.development.yaml"


class ContextConfigError(ValueError):
    pass


class _ContextRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config_version: Annotated[str, Field(min_length=1)]
    protocol_version: Literal["1.0.0"]
    development_only: Literal[True]
    context: ContextConfig


def load_context_config(path: Path | str | None = None) -> ContextConfig:
    config_path = Path(path) if path is not None else DEFAULT_CONTEXT_CONFIG
    try:
        document: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return _ContextRuntimeConfig.model_validate(document).context
    except OSError as exc:
        raise ContextConfigError(f"cannot read context config: {exc.strerror or exc}") from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise ContextConfigError(f"context config failed validation: {exc}") from exc
