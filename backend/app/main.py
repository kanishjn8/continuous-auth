"""Process liveness plus an explicit factory for the authenticated T-016 API."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI

from protocol.generated.python.contracts import PROTOCOL_VERSION

from .api.backend import SQLiteApiBackend
from .api.config import load_api_settings
from .api.routes import create_api_app
from .storage.config import load_storage_settings
from .storage.service import StorageService
from .updates.manager import UpdateManager

app = FastAPI(
    title="Continuous Authentication Backend",
    version=PROTOCOL_VERSION,
    docs_url=None,
    redoc_url=None,
)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    """Return process liveness only; protection health belongs to C7 `/v1/health`."""

    return {"status": "alive", "protocol_version": PROTOCOL_VERSION}


def create_runtime_app(
    *,
    local_secret: str,
    active_user_provider: Callable[[], str | None],
    storage_config: Path,
    workspace_root: Path,
    api_config: Path | None = None,
    update_manager: UpdateManager | None = None,
    shadow_mode_setter: Callable[[bool], None] | None = None,
) -> FastAPI:
    """Open local persistence and return the complete authenticated API/stream app."""

    storage_settings = load_storage_settings(storage_config, workspace_root=workspace_root)
    storage = StorageService.open(storage_settings)
    backend = SQLiteApiBackend(
        storage,
        active_user_provider=active_user_provider,
        update_manager=update_manager,
        shadow_mode_setter=shadow_mode_setter,
    )
    return create_api_app(
        settings=load_api_settings(api_config),
        backend=backend,
        local_secret=local_secret,
    )
