"""Wire C1 ingestion through shared features, scoring, risk, storage, and actions."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from backend.app.decisions.adapters import EnforcementCoordinator
from backend.app.ingestion.config import IngestionSettings
from backend.app.ingestion.pipeline import IngestionPipeline
from backend.app.ingestion.types import (
    AttributedEvent,
    AuthenticatedEntry,
    AvailabilityEvent,
    IngestionBatch,
    LifecycleEvent,
)
from backend.app.models.service import ModelScoringService, ProfileArtifacts
from backend.app.risk.config import RiskSettings
from backend.app.risk.context import ContextConfidenceLayer
from backend.app.risk.engine import RiskEngine
from backend.app.risk.state import UserStateMachine
from backend.app.risk.types import DecisionInput, RiskAlert
from backend.app.storage.service import StorageService
from ml.features.config import MLConfig
from ml.features.extractor import window_config_from_ml_config
from ml.features.schema import ContextEvent, FeatureWindow, Heartbeat, KeyboardEvent, MouseEvent
from ml.features.windowing import WindowBuilder
from protocol.generated.python.contracts import (
    Alert,
    AlertType,
    AppRegistryEntry,
    AppRegistryEvent,
    ContextConfig,
    DataProvenance,
    DeviceMetadataEvent,
    Health,
    Metrics,
    StreamEventType,
    UserState,
    VerificationAnchor,
    WebSocketPayload,
)

from .measurements import RuntimeMeasurements

ProfileProvider = Callable[[str], ProfileArtifacts | None]


@dataclass(frozen=True)
class StreamEmission:
    event_type: StreamEventType
    payload: WebSocketPayload


class RuntimeOrchestrator:
    """Single-session coordinator; all ordered event state remains memory-only."""

    def __init__(
        self,
        *,
        storage: StorageService,
        ingestion_settings: IngestionSettings,
        ml_config: MLConfig,
        risk_settings: RiskSettings,
        context_config: ContextConfig,
        provenance: DataProvenance,
        profile_provider: ProfileProvider,
        heartbeat_timeout_seconds: float,
        measurement_capacity: int,
        enforcement: EnforcementCoordinator | None = None,
    ) -> None:
        if provenance not in {DataProvenance.SYNTHETIC, DataProvenance.TEAM, DataProvenance.PILOT}:
            raise ValueError("live runtime provenance must be synthetic, team, or pilot")
        if heartbeat_timeout_seconds <= 0:
            raise ValueError("heartbeat timeout must be positive")
        self.storage = storage
        self.ml_config = ml_config
        self.risk_settings = risk_settings
        self.context_layer = ContextConfidenceLayer(context_config)
        self.provenance = provenance
        self.profile_provider = profile_provider
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.measurements = RuntimeMeasurements(measurement_capacity)
        self.enforcement = enforcement or EnforcementCoordinator({}, store=storage)
        self._active_user: str | None = None
        self._session_wall_anchor: datetime | None = None
        self._builders: dict[str, WindowBuilder] = {}
        self._risk_engine: RiskEngine | None = None
        self._scoring: ModelScoringService | None = None
        self._profile_available = False
        self._profile_failure_reported = False
        self._device_resolution: tuple[int, int] | None = None
        self._last_heartbeat_arrival: float | None = None
        self._heartbeat_failed = False
        self._emissions: list[StreamEmission] = []
        self.pipeline = IngestionPipeline(
            ingestion_settings,
            event_sink=self._on_event,
            lifecycle_sink=self._on_lifecycle,
            availability_sink=self._on_ingestion_availability,
        )

    @property
    def active_user_id(self) -> str | None:
        return self._active_user

    def _drain_emissions(self) -> tuple[StreamEmission, ...]:
        emissions = tuple(self._emissions)
        self._emissions.clear()
        return emissions

    def _stored_state(self, user_id: str) -> UserState | None:
        with self.storage.database.connection() as connection:
            row = connection.execute(
                "SELECT state FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return None if row is None else UserState(row["state"])

    def start_authenticated_session(
        self,
        *,
        user_id: str,
        evidence_reference: str,
        authenticated_at: datetime | None = None,
        session_id: str | None = None,
    ) -> tuple[LifecycleEvent, tuple[StreamEmission, ...]]:
        observed = authenticated_at or datetime.now(UTC)
        if observed.tzinfo is None:
            raise ValueError("authenticated_at must include a timezone")
        state = self._stored_state(user_id)
        if state is None:
            state = UserState.ENROLLING
            self.storage.upsert_user(user_id, state)
        try:
            profile = self.profile_provider(user_id)
        except Exception:
            profile = None
            self._profile_failure_reported = True
            self._on_ingestion_availability(
                AvailabilityEvent(
                    code="MODEL_LOAD_FAILED",
                    component="model",
                    detail="active profile could not be loaded or verified",
                )
            )
        self._profile_available = profile is not None
        resolved = profile or ProfileArtifacts(
            user_id=user_id,
            profile_version="unavailable",
            model_version="unavailable",
            keyboard=None,
            mouse=None,
        )
        self._scoring = ModelScoringService(resolved)
        self._risk_engine = RiskEngine(
            user_id=user_id,
            settings=self.risk_settings,
            state_machine=UserStateMachine(self.risk_settings.enrollment, initial=state),
        )
        self._active_user = user_id
        self._session_wall_anchor = observed.astimezone(UTC)
        lifecycle = self.pipeline.start_session(
            AuthenticatedEntry(
                user_id=user_id,
                evidence_id=VerificationAnchor.A1_LOGIN_UNLOCK.value,
                authenticated=True,
                wall_clock_anchor=observed,
                session_id=session_id,
            )
        )
        assert lifecycle.session_id is not None
        self.enforcement.record_authenticated_entry(
            user_id=user_id,
            session_id=lifecycle.session_id,
            segment_id=None,
            evidence_reference=evidence_reference,
            authenticated_at=observed,
        )
        return lifecycle, self._drain_emissions()

    def end_session(self, reason: str = "AUTHENTICATED_EXIT") -> tuple[StreamEmission, ...]:
        self.pipeline.end_session(reason)
        self._active_user = None
        self._session_wall_anchor = None
        self._risk_engine = None
        self._scoring = None
        self._profile_available = False
        self._profile_failure_reported = False
        self._builders.clear()
        return self._drain_emissions()

    def feed(
        self, chunk: bytes | bytearray | memoryview
    ) -> tuple[IngestionBatch, tuple[StreamEmission, ...]]:
        started = time.perf_counter_ns()
        try:
            batch = self.pipeline.feed(chunk)
            return batch, self._drain_emissions()
        finally:
            self.measurements.record("frame_batch", (time.perf_counter_ns() - started) / 1000)

    def transport_disconnected(self) -> tuple[IngestionBatch, tuple[StreamEmission, ...]]:
        batch = self.pipeline.recover_transport()
        return batch, self._drain_emissions()

    def shutdown(self) -> tuple[StreamEmission, ...]:
        self.pipeline.shutdown()
        self._active_user = None
        self._builders.clear()
        return self._drain_emissions()

    def _on_lifecycle(self, event: LifecycleEvent) -> None:
        if event.kind == "SESSION_STARTED":
            assert event.session_id is not None
            self.storage.create_session(
                event.session_id,
                event.user_id,
                VerificationAnchor.A1_LOGIN_UNLOCK,
                started_at=event.wall_clock_anchor,
            )
        elif event.kind == "SEGMENT_STARTED":
            assert event.segment_id is not None and event.session_id is not None
            assert event.t_capture_us is not None and self._session_wall_anchor is not None
            self.storage.create_segment(
                event.segment_id,
                event.session_id,
                event.t_capture_us,
                event.reason,
            )
            builder = WindowBuilder(
                window_config_from_ml_config(self.ml_config),
                user_id=event.user_id,
                session_id=event.session_id,
                segment_id=event.segment_id,
                collection_day=self._session_wall_anchor.date().isoformat(),
                provenance=self.provenance,
                device_resolution=self._device_resolution,
            )
            self._builders[event.segment_id] = builder
        elif event.kind == "SEGMENT_ENDED":
            assert event.segment_id is not None and event.t_capture_us is not None
            closed_builder = (
                self._builders.pop(event.segment_id) if event.segment_id in self._builders else None
            )
            if closed_builder is not None:
                trailing = closed_builder.flush()
                if trailing is not None:
                    self._process_window(trailing)
            self.storage.end_segment(event.segment_id, event.t_capture_us, event.reason)
        elif event.kind == "SESSION_ENDED":
            assert event.session_id is not None
            self.storage.end_session(event.session_id)

    def _on_event(self, attributed: AttributedEvent) -> None:
        event = attributed.event
        builder = self._builders.get(attributed.segment_id)
        if isinstance(event, AppRegistryEvent):
            self.storage.register_app(
                AppRegistryEntry(
                    schema_version=event.schema_version,
                    app_id=event.app_id,
                    process_name=event.process_name,
                    category=event.category,
                )
            )
        elif isinstance(event, DeviceMetadataEvent):
            self._device_resolution = (event.screen_width_px, event.screen_height_px)
            if builder is not None:
                builder.set_device_resolution(event.screen_width_px, event.screen_height_px)
        elif isinstance(event, ContextEvent):
            if builder is not None:
                builder.push_context_event(event)
        elif isinstance(event, Heartbeat):
            self._handle_heartbeat(event)
        elif isinstance(event, (KeyboardEvent, MouseEvent)) and builder is not None:
            for window in builder.push_events([event]):
                self._process_window(window)

    def _handle_heartbeat(self, event: Heartbeat) -> None:
        self._last_heartbeat_arrival = time.monotonic()
        self._heartbeat_failed = False
        health = Health(
            status="DEGRADED" if event.collection_paused else "HEALTHY",
            components={
                "collector": "DEGRADED" if event.collection_paused else "HEALTHY",
                "storage": "HEALTHY",
                "model": "HEALTHY" if self._scoring is not None else "UNAVAILABLE",
            },
            heartbeat_age_ms=0,
            collection_paused=event.collection_paused,
        )
        self.storage.record_health("runtime", datetime.now(UTC), health)
        self.storage.record_metrics(
            "runtime",
            datetime.now(UTC),
            Metrics(
                collector_cpu_percent=None,
                collector_memory_bytes=None,
                dropped_events=event.dropped_events,
                latency_us=self.measurements.snapshot(),
            ),
        )
        self._emissions.append(StreamEmission(StreamEventType.HEALTH, health))

    def check_heartbeat(self, now: float | None = None) -> tuple[StreamEmission, ...]:
        current = time.monotonic() if now is None else now
        age = (
            None if self._last_heartbeat_arrival is None else current - self._last_heartbeat_arrival
        )
        if age is None or age <= self.heartbeat_timeout_seconds or self._heartbeat_failed:
            return self._drain_emissions()
        self._heartbeat_failed = True
        if self._risk_engine is not None:
            self._store_risk_alert(
                self._risk_engine.heartbeat_lost(
                    t_capture_us=0,
                    detail="collector heartbeat exceeded configured arrival timeout",
                )
            )
        health = Health(
            status="UNAVAILABLE",
            components={"collector": "UNAVAILABLE", "storage": "HEALTHY", "model": "DEGRADED"},
            heartbeat_age_ms=int(age * 1000),
            collection_paused=False,
        )
        self.storage.record_health("runtime", datetime.now(UTC), health)
        self._emissions.append(StreamEmission(StreamEventType.HEALTH, health))
        return self._drain_emissions()

    def _progress_state(self, *, after_score: bool) -> None:
        if self._active_user is None or self._risk_engine is None:
            return
        if not self._profile_available:
            return
        with self.storage.database.connection() as connection:
            counts = connection.execute(
                """
                SELECT COUNT(*) AS windows, COUNT(DISTINCT collection_day) AS days
                FROM feature_windows WHERE user_id = ?
                """,
                (self._active_user,),
            ).fetchone()
            scored = connection.execute(
                "SELECT COUNT(*) AS scored FROM scores WHERE user_id = ?",
                (self._active_user,),
            ).fetchone()
        transition = self._risk_engine.state_machine.observe_progress(
            enrollment_windows=int(counts["windows"]),
            distinct_days=int(counts["days"]),
            calibration_windows=int(scored["scored"]) if after_score else 0,
        )
        if transition is not None:
            self.storage.upsert_user(self._active_user, transition.current)

    def _process_window(self, window: FeatureWindow) -> None:
        started = time.perf_counter_ns()
        try:
            self._process_window_inner(window)
        finally:
            self.measurements.record(
                "feature_score_decision", (time.perf_counter_ns() - started) / 1000
            )

    def _process_window_inner(self, window: FeatureWindow) -> None:
        self.storage.store_feature_window(window)
        self._refresh_profile(window.user_id)
        self._progress_state(after_score=False)
        assert self._scoring is not None and self._risk_engine is not None
        scoring = self._scoring.score(window)
        self.storage.store_score(scoring.score)
        self._progress_state(after_score=True)
        context = self.context_layer.assess(
            user_id=window.user_id,
            category=window.context.dominant_category,
        )
        outcome = self._risk_engine.process(
            DecisionInput(
                score=scoring.score,
                session_id=window.session_id,
                segment_id=window.segment_id,
                t_decision_us=window.t_end_us,
                context=context.assessment,
            )
        )
        if outcome.decision is None:
            return
        self.storage.store_risk_decision(outcome.decision)
        self.enforcement.execute(outcome.decision)
        for alert in outcome.alerts:
            self._store_risk_alert(alert)
        self._emissions.append(StreamEmission(StreamEventType.RISK, outcome.decision))

    def _refresh_profile(self, user_id: str) -> None:
        try:
            profile = self.profile_provider(user_id)
        except Exception:
            self._profile_available = False
            self._scoring = ModelScoringService(
                ProfileArtifacts(user_id, "unavailable", "unavailable", None, None)
            )
            if not self._profile_failure_reported:
                self._profile_failure_reported = True
                self._on_ingestion_availability(
                    AvailabilityEvent(
                        code="MODEL_LOAD_FAILED",
                        component="model",
                        detail="active profile could not be loaded or verified",
                    )
                )
            return
        self._profile_failure_reported = False
        self._profile_available = profile is not None
        if profile is None:
            self._scoring = ModelScoringService(
                ProfileArtifacts(user_id, "unavailable", "unavailable", None, None)
            )
            return
        if (
            self._scoring is None
            or self._scoring.profile.profile_version != profile.profile_version
        ):
            self._scoring = ModelScoringService(profile)

    def _on_ingestion_availability(self, event: AvailabilityEvent) -> None:
        alert = Alert(
            alert_id=f"alert-{uuid.uuid4()}",
            alert_type=AlertType.AVAILABILITY,
            severity="HIGH",
            code=event.code,
            occurred_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            acknowledged=False,
        )
        self.storage.store_alert(alert)
        self._emissions.append(StreamEmission(StreamEventType.ALERT, alert))

    def _store_risk_alert(self, source: RiskAlert) -> None:
        alert = Alert(
            alert_id=f"alert-{uuid.uuid4()}",
            alert_type=source.alert_type,
            severity=source.severity,
            code=source.code,
            occurred_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            acknowledged=False,
        )
        self.storage.store_alert(alert)
        self._emissions.append(StreamEmission(StreamEventType.ALERT, alert))
