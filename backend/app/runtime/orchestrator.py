"""Wire C1 ingestion through shared features, scoring, risk, storage, and actions."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from backend.app.decisions.adapters import (
    ActionAdapter,
    ActionStatus,
    AdapterResult,
    EnforcementCoordinator,
    EnforcementNotice,
    VerificationRecord,
)
from backend.app.decisions.challenge import ChallengeError, ChallengeService, PendingChallenge
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
from backend.app.updates.anchors import ScheduledAnchorScheduler
from backend.app.updates.manager import SegmentEvidence, UpdateManager
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
    DecisionAction,
    DeviceMetadataEvent,
    Health,
    Metrics,
    RiskLevel,
    ScoreResult,
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
        enforcement_adapters: Mapping[DecisionAction, ActionAdapter] | None = None,
        update_manager: UpdateManager | None = None,
        anchor_scheduler: ScheduledAnchorScheduler | None = None,
        challenge_service: ChallengeService | None = None,
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
        self.enforcement = enforcement or EnforcementCoordinator(
            dict(enforcement_adapters or {}),
            store=storage,
            notice_sink=self._on_enforcement_notice,
        )
        self.update_manager = update_manager
        self.anchor_scheduler = anchor_scheduler
        self.challenge_service = challenge_service
        self._shadow_mode = storage.shadow_mode_enabled()
        self._active_user: str | None = None
        self._session_id: str | None = None
        self._session_wall_anchor: datetime | None = None
        self._builders: dict[str, WindowBuilder] = {}
        self._segment_gap_baseline: dict[str, int] = {}
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

    def set_shadow_mode(self, enabled: bool) -> None:
        """Apply the already-persisted administrative mode to current and future sessions."""

        self._shadow_mode = enabled
        if self._risk_engine is not None:
            self._risk_engine.set_shadow_mode(enabled)

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
        self._risk_engine.set_shadow_mode(self._shadow_mode)
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
        self._session_id = lifecycle.session_id
        if self.anchor_scheduler is not None:
            self.anchor_scheduler.session_started(
                session_id=lifecycle.session_id, at=observed
            )
        return lifecycle, self._drain_emissions()

    def end_session(self, reason: str = "AUTHENTICATED_EXIT") -> tuple[StreamEmission, ...]:
        self.pipeline.end_session(reason)
        if self.anchor_scheduler is not None and self._session_id is not None:
            self.anchor_scheduler.session_ended(session_id=self._session_id)
        self._active_user = None
        self._session_id = None
        self._session_wall_anchor = None
        self._risk_engine = None
        self._scoring = None
        self._profile_available = False
        self._profile_failure_reported = False
        self._builders.clear()
        self._segment_gap_baseline.clear()
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
        self._segment_gap_baseline.clear()
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
            self._segment_gap_baseline[event.segment_id] = self.pipeline.counters.missing_sequences
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
            self._submit_segment_candidate(event.segment_id)
        elif event.kind == "SESSION_ENDED":
            assert event.session_id is not None
            self.storage.end_session(event.session_id)

    def _submit_segment_candidate(self, segment_id: str) -> None:
        if self.update_manager is None:
            self._segment_gap_baseline.pop(segment_id, None)
            return
        gap_baseline = self._segment_gap_baseline.pop(segment_id, 0)
        try:
            with self.storage.database.connection() as connection:
                segment = connection.execute(
                    """
                    SELECT segments.session_id, sessions.user_id
                    FROM segments JOIN sessions USING(session_id)
                    WHERE segments.segment_id = ?
                    """,
                    (segment_id,),
                ).fetchone()
                if segment is None:
                    raise ValueError("completed segment metadata is unavailable")
                risk_rows = connection.execute(
                    """
                    SELECT risk_level FROM risk_events
                    WHERE segment_id = ? ORDER BY t_decision_us
                    """,
                    (segment_id,),
                ).fetchall()
                score_rows = connection.execute(
                    """
                    SELECT scores.score_json FROM scores
                    JOIN feature_windows USING(window_id)
                    WHERE feature_windows.segment_id = ?
                    """,
                    (segment_id,),
                ).fetchall()
                enforcement_triggered = bool(
                    connection.execute(
                        """
                        SELECT EXISTS(
                            SELECT 1 FROM decisions JOIN risk_events USING(decision_id)
                            WHERE risk_events.segment_id = ?
                              AND decisions.enforcement_applied = 1
                        )
                        """,
                        (segment_id,),
                    ).fetchone()[0]
                )
                first_segment = connection.execute(
                    """
                    SELECT segment_id FROM segments WHERE session_id = ?
                    ORDER BY started_at_capture_us LIMIT 1
                    """,
                    (segment["session_id"],),
                ).fetchone()
                allow_entry_anchor = (
                    first_segment is not None and first_segment["segment_id"] == segment_id
                )
                anchor = connection.execute(
                    """
                    SELECT * FROM verification_anchors
                    WHERE user_id = ? AND session_id = ?
                      AND (segment_id = ? OR (? = 1 AND segment_id IS NULL))
                    ORDER BY authenticated_at_utc DESC LIMIT 1
                    """,
                    (
                        segment["user_id"],
                        segment["session_id"],
                        segment_id,
                        int(allow_entry_anchor),
                    ),
                ).fetchone()
            verification = (
                None
                if anchor is None
                else VerificationRecord(
                    anchor_id=anchor["anchor_id"],
                    user_id=anchor["user_id"],
                    session_id=anchor["session_id"],
                    segment_id=anchor["segment_id"],
                    anchor_type=VerificationAnchor(anchor["anchor_type"]),
                    evidence_reference=anchor["evidence_reference"],
                    authenticated_at=datetime.fromisoformat(
                        str(anchor["authenticated_at_utc"]).replace("Z", "+00:00")
                    ),
                )
            )
            scored_windows = sum(
                1
                for row in score_rows
                if (
                    (score := ScoreResult.model_validate_json(row["score_json"])).keyboard.available
                    or score.mouse.available
                )
            )
            self.update_manager.submit_segment(
                SegmentEvidence(
                    user_id=segment["user_id"],
                    session_id=segment["session_id"],
                    segment_id=segment_id,
                    completed_at=datetime.now(UTC),
                    risk_levels=tuple(RiskLevel(row["risk_level"]) for row in risk_rows),
                    scored_windows=scored_windows,
                    enforcement_triggered=enforcement_triggered,
                    unexplained_gap=self.pipeline.counters.missing_sequences > gap_baseline,
                    verification=verification,
                )
            )
        except Exception:
            self._on_ingestion_availability(
                AvailabilityEvent(
                    code="UPDATE_CANDIDATE_ADMISSION_FAILED",
                    component="update_manager",
                    detail="completed segment could not be evaluated for update eligibility",
                )
            )

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
            self.check_scheduled_anchor()
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

    def check_scheduled_anchor(self, now: datetime | None = None) -> PendingChallenge | None:
        """Open an A3 prompt when one is due, or return None.

        Called from the heartbeat path so it runs on the existing periodic
        tick rather than adding a timer. Any failure is swallowed into an
        availability signal: a missing verification prompt must never take
        down ingestion (ADR-011, fail-open).
        """

        if self.anchor_scheduler is None or self.challenge_service is None:
            return None
        if self._active_user is None or self._session_id is None:
            return None
        segment_id = next(iter(self._builders), None)
        if segment_id is None:
            return None
        instant = now or datetime.now(UTC)
        if not self.anchor_scheduler.due(session_id=self._session_id, now=instant):
            return None
        try:
            return self.challenge_service.open_scheduled(
                user_id=self._active_user,
                session_id=self._session_id,
                segment_id=segment_id,
            )
        except ChallengeError:
            self._on_ingestion_availability(
                AvailabilityEvent(
                    code="SCHEDULED_ANCHOR_UNAVAILABLE",
                    component="verification",
                    detail="scheduled verification prompt could not be opened",
                )
            )
            return None

    def complete_scheduled_anchor(
        self,
        *,
        decision_id: str,
        session_id: str,
        segment_id: str,
        evidence_reference: str,
        at: datetime | None = None,
    ) -> None:
        """Record an A3 anchor for a correctly answered scheduled prompt."""

        del decision_id
        if self._active_user is None:
            return
        instant = at or datetime.now(UTC)
        verification = self.enforcement.record_scheduled_verification(
            user_id=self._active_user,
            session_id=session_id,
            segment_id=segment_id,
            result=AdapterResult(
                ActionStatus.SUCCEEDED,
                "SCHEDULED_VERIFICATION_ACCEPTED",
                evidence_reference,
            ),
            authenticated_at=instant,
        )
        if verification is not None and self.anchor_scheduler is not None:
            self.anchor_scheduler.anchor_recorded(session_id=session_id, at=instant)

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
            shares=window.context.app_shares,
            dominant_category=window.context.dominant_category,
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
        if outcome.decision.risk_level == RiskLevel.LOW and outcome.decision.fused_score is not None:
            # ADR-008 empirical layer: accumulate this user's own genuine
            # (LOW-risk) score statistics per application so
            # ContextConfidenceLayer can supersede the static bootstrap map
            # once enough observations exist. Gated on LOW risk rather than
            # "during enrollment" (the ADR's original phrasing) because the
            # implemented state machine never produces a decision at all
            # during ENROLLING -- decisions only start in CALIBRATING/ACTIVE,
            # so that is where genuine variance is actually observable.
            #
            # Attribution is per-application and focus-weighted, so a window
            # spanning a switch feeds partial evidence to each application it
            # actually covered rather than all of it to the dominant one.
            self.context_layer.observe_genuine(
                user_id=window.user_id,
                shares=window.context.app_shares,
                risk_score=outcome.decision.fused_score,
            )
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

    def _on_enforcement_notice(self, notice: EnforcementNotice) -> None:
        alert = Alert(
            alert_id=f"alert-{uuid.uuid4()}",
            alert_type=AlertType.AVAILABILITY,
            severity=notice.severity,
            code=notice.code,
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
