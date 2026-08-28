"""Container entry point for the authenticated dashboard control plane."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from ml.features.config import load_config as load_ml_config

from .main import create_runtime_app

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _configured_path(variable: str, default: Path) -> Path:
    value = os.environ.get(variable)
    return Path(value) if value else default


def create_app() -> FastAPI:
    """Create the Compose API and fail startup on missing secret or invalid ML config."""

    local_secret = os.environ.get("CA_DASHBOARD_SECRET")
    if local_secret is None or not local_secret:
        raise RuntimeError("CA_DASHBOARD_SECRET must be set")

    ml_config_path = _configured_path("CA_ML_CONFIG", WORKSPACE_ROOT / "config/ml.development.yaml")
    load_ml_config(ml_config_path)

    return create_runtime_app(
        local_secret=local_secret,
        active_user_provider=lambda: None,
        storage_config=_configured_path(
            "CA_STORAGE_CONFIG", WORKSPACE_ROOT / "config/storage.development.yaml"
        ),
        workspace_root=WORKSPACE_ROOT,
        api_config=_configured_path(
            "CA_API_CONFIG", WORKSPACE_ROOT / "config/api.development.yaml"
        ),
    )
