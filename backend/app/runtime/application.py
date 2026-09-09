"""Construct the complete collection API plus collector runtime."""

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
from backend.app.decisions.adapters import ActionAdapter
from backend.app.decisions.challenge import ChallengeService
from backend.app.decisions.config import load_enforcement_settings
from backend.app.decisions.native import NativeChallengeAdapter, WindowsLockAdapter
from backend.app.ingestion.config import load_ingestion_settings
from backend.app.risk.config import load_risk_settings
from backend.app.risk.context_config import load_context_config
from backend.app.storage.config import load_storage_settings
from backend.app.storage.service import StorageService
from backend.app.updates.anchors import ScheduledAnchorScheduler
from backend.app.updates.config import load_update_settings
from backend.app.updates.manager import UpdateManager
from backend.app.updates.repository import SQLiteUpdateRepository
from ml.features.config import load_config as load_ml_config
from protocol.generated.python.contracts import DecisionAction

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


def create_collection_application(
    *,
    local_secret: str,
    participant_id: str,
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
    enforcement_config: Path | None = None,
    dashboard_directory: Path | None = None,
    drill_label: str | None = None,
) -> IntegratedApplication:
    """Build the end-to-end runtime for one participant or synthetic user.

    What the run produces is decided entirely by ``storage_config``: an
    approved-collection profile records real PILOT data, a synthetic-only
    profile records SYNTHETIC development data. This function never chooses.

    ``drill_label`` declares the whole run a live attacker drill (ADR-014).
    Its windows are scored, risk-assessed, escalated and enforced exactly as
    normal -- and are permanently excluded from every training, calibration,
    validation, enrollment and update corpus. It is opt-in and per-process:
    never pass it for genuine collection.
    """

    if not participant_id.strip() or not local_secret:
        raise ValueError("participant identifier and dashboard secret are required")
    storage_settings = load_storage_settings(storage_config, workspace_root=workspace_root)
    storage = StorageService.open(storage_settings)
    # Derived from the storage profile, never chosen here: an approved-collection
    # store yields PILOT, a synthetic-only store yields SYNTHETIC. Selecting the
    # storage config is therefore the single act that decides provenance.
    provenance = storage_settings.collection_provenance
    orchestration = load_orchestration_settings(orchestration_config)
    collector = load_collector_config(collector_config)
    profile_provider = DirectoryProfileProvider(storage, artifact_root)
    update_settings = load_update_settings(updates_config)
    anchor_scheduler = ScheduledAnchorScheduler(
        update_settings.update_manager.scheduled_anchor_interval_seconds
    )
    update_manager = UpdateManager(update_settings, SQLiteUpdateRepository(storage))
    api_settings = load_api_settings(api_config)
    enforcement_settings = load_enforcement_settings(enforcement_config)
    challenge_service = ChallengeService(storage, enforcement_settings)
    response_endpoint = (
        f"http://{api_settings.api.bind_host}:{api_settings.api.port:d}/v1/enforcement/challenge"
    )
    enforcement_adapters: dict[DecisionAction, ActionAdapter] = {
        action: NativeChallengeAdapter(
            action,
            service=challenge_service,
            settings=enforcement_settings,
            response_endpoint=response_endpoint,
        )
        for action in (DecisionAction.SOFT_CHALLENGE, DecisionAction.REAUTH)
    }
    enforcement_adapters[DecisionAction.TERMINATE] = WindowsLockAdapter(enforcement_settings)
    orchestrator = RuntimeOrchestrator(
        storage=storage,
        ingestion_settings=load_ingestion_settings(ingestion_config),
        ml_config=load_ml_config(ml_config),
        risk_settings=load_risk_settings(risk_config),
        context_config=load_context_config(context_config),
        provenance=provenance,
        profile_provider=profile_provider,
        heartbeat_timeout_seconds=orchestration.heartbeat_timeout_seconds,
        measurement_capacity=orchestration.measurement_capacity,
        enforcement_adapters=enforcement_adapters,
        update_manager=update_manager,
        anchor_scheduler=anchor_scheduler,
        challenge_service=challenge_service,
        drill_label=drill_label,
    )
    backend = SQLiteApiBackend(
        storage,
        active_user_provider=lambda: orchestrator.active_user_id,
        update_manager=update_manager,
        shadow_mode_setter=orchestrator.set_shadow_mode,
        challenge_service=challenge_service,
        enforcement=orchestrator.enforcement,
        enforcement_settings=enforcement_settings,
        scheduled_anchor_sink=orchestrator.complete_scheduled_anchor,
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
            user_id=participant_id,
            evidence_reference=f"session-entry-{secrets.token_urlsafe(32)}",
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
