"""Construct the complete synthetic-development API plus collector runtime."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.app.api.backend import SQLiteApiBackend
from backend.app.api.config import ApiSettings, load_api_settings
from backend.app.api.routes import create_api_app
from backend.app.ingestion.config import load_ingestion_settings
from backend.app.risk.config import load_risk_settings
from backend.app.risk.context_config import load_context_config
from backend.app.storage.config import load_storage_settings
from backend.app.storage.service import StorageService
from backend.app.updates.config import load_update_settings
from backend.app.updates.manager import UpdateManager
from backend.app.updates.repository import SQLiteUpdateRepository
from ml.features.config import load_config as load_ml_config
from protocol.generated.python.contracts import DataProvenance

from .collector_config import load_collector_config
from .config import load_orchestration_settings
from .named_pipe import WindowsNamedPipeServer
from .orchestrator import RuntimeOrchestrator
from .profiles import DirectoryProfileProvider
from .service import IntegratedRuntimeService


@dataclass(frozen=True)
class IntegratedApplication:
    app: FastAPI
    api_settings: ApiSettings
    orchestrator: RuntimeOrchestrator
    service: IntegratedRuntimeService


def create_synthetic_development_application(
    *,
    local_secret: str,
    synthetic_user_id: str,
    workspace_root: Path,
    artifact_root: Path,
    storage_config: Path,
    collector_config: Path,
    ingestion_config: Path,
    ml_config: Path,
    risk_config: Path,
    context_config: Path,
    api_config: Path,
    updates_config: Path,
    orchestration_config: Path,
    dashboard_directory: Path | None = None,
) -> IntegratedApplication:
    """Build E2E development mode; it cannot be used for participant provenance."""

    if not synthetic_user_id.strip() or not local_secret:
        raise ValueError("synthetic user and dashboard secret are required")
    storage = StorageService.open(
        load_storage_settings(storage_config, workspace_root=workspace_root)
    )
    orchestration = load_orchestration_settings(orchestration_config)
    collector = load_collector_config(collector_config)
    profile_provider = DirectoryProfileProvider(storage, artifact_root)
    update_manager = UpdateManager(
        load_update_settings(updates_config), SQLiteUpdateRepository(storage)
    )
    orchestrator = RuntimeOrchestrator(
        storage=storage,
        ingestion_settings=load_ingestion_settings(ingestion_config),
        ml_config=load_ml_config(ml_config),
        risk_settings=load_risk_settings(risk_config),
        context_config=load_context_config(context_config),
        provenance=DataProvenance.SYNTHETIC,
        profile_provider=profile_provider,
        heartbeat_timeout_seconds=orchestration.heartbeat_timeout_seconds,
        measurement_capacity=orchestration.measurement_capacity,
        update_manager=update_manager,
    )
    api_settings = load_api_settings(api_config)
    backend = SQLiteApiBackend(
        storage,
        active_user_provider=lambda: orchestrator.active_user_id,
        update_manager=update_manager,
        shadow_mode_setter=orchestrator.set_shadow_mode,
    )
    app = create_api_app(
        settings=api_settings,
        backend=backend,
        local_secret=local_secret,
    )
    if dashboard_directory is not None:
        if not (dashboard_directory / "index.html").is_file():
            raise ValueError("dashboard directory does not contain a built index")
        app.mount("/", StaticFiles(directory=dashboard_directory, html=True), name="dashboard")
    pipe = WindowsNamedPipeServer(
        collector.pipe_name,
        read_bytes=orchestration.pipe_read_bytes,
        buffer_bytes=orchestration.pipe_buffer_bytes,
    )
    broker = app.state.api_context.broker
    service = IntegratedRuntimeService(
        orchestrator=orchestrator,
        pipe=pipe,
        broker=broker,
        watchdog_interval_seconds=orchestration.watchdog_interval_seconds,
    )
    task: asyncio.Task[None] | None = None

    async def start_runtime() -> None:
        nonlocal task
        _, emissions = orchestrator.start_authenticated_session(
            user_id=synthetic_user_id,
            evidence_reference=f"synthetic-entry-{secrets.token_urlsafe(32)}",
        )
        await service.publish(emissions)
        task = asyncio.create_task(service.run(), name="integrated-runtime")

    async def stop_runtime() -> None:
        service.stop()
        if task is not None:
            await task

    app.router.add_event_handler("startup", start_runtime)
    app.router.add_event_handler("shutdown", stop_runtime)
    return IntegratedApplication(app, api_settings, orchestrator, service)
