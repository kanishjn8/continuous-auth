"""Deterministic C3-to-C4 risk engine with fail-open/loud failure handling."""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Callable

from backend.app.decisions.policy import DecisionPolicy
from protocol.generated.python.contracts import (
    PROTOCOL_VERSION,
    AlertType,
    DecisionAction,
    ModelStatus,
    RiskDecision,
    RiskLevel,
    UserState,
    WindowQuality,
)

from .config import RiskSettings
from .state import UserStateMachine
from .types import DecisionInput, DecisionTrace, RiskAlert, RiskOutcome

IdFactory = Callable[[], str]
DecisionSink = Callable[[RiskDecision], None]
AlertSink = Callable[[RiskAlert], None]


def _decision_id() -> str:
    return f"decision-{uuid.uuid4()}"


_RANK = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


class RiskEngine:
    def __init__(
        self,
        *,
        user_id: str,
        settings: RiskSettings,
        state_machine: UserStateMachine | None = None,
        decision_sink: DecisionSink | None = None,
        alert_sink: AlertSink | None = None,
        id_factory: IdFactory | None = None,
    ):
        if not user_id.strip():
            raise ValueError("risk engine user_id must not be blank")
        self.user_id = user_id
        self.settings = settings
        self.state_machine = state_machine or UserStateMachine(settings.enrollment)
        self._policy = DecisionPolicy(settings)
        self._decision_sink = decision_sink
        self._alert_sink = alert_sink
        self._id_factory = id_factory or _decision_id
        self._history = deque[float](maxlen=settings.risk.breach_n)
        self._smoothed: float | None = None
        self._risk_level = RiskLevel.LOW
        self._last_decision_us: int | None = None

    def _emit_alert(self, alert: RiskAlert) -> None:
        if self._alert_sink is not None:
            self._alert_sink(alert)

    def _required_failure(self, request: DecisionInput) -> str | None:
        score = request.score
        required = []
        if score.quality_label in (WindowQuality.FULL, WindowQuality.KBD_ONLY):
            required.append(("keyboard", score.keyboard))
        if score.quality_label in (WindowQuality.FULL, WindowQuality.MOUSE_ONLY):
            required.append(("mouse", score.mouse))
        for name, modality in required:
            if not modality.available or modality.status != ModelStatus.SCORED:
                return f"{name} model status is {modality.status.value}"
        return None

    def _fusion(self, request: DecisionInput) -> tuple[float, float, float]:
        score = request.score
        weighted: list[tuple[float, float]] = []
        if score.keyboard.available and score.keyboard.calibrated_score is not None:
            weighted.append((score.keyboard.calibrated_score, self.settings.risk.keyboard_weight))
        if score.mouse.available and score.mouse.calibrated_score is not None:
            weighted.append((score.mouse.calibrated_score, self.settings.risk.mouse_weight))
        total = sum(weight for _, weight in weighted)
        if not weighted or total <= 0:
            raise ValueError("no positively weighted calibrated modality is available")
        fused = sum(value * weight for value, weight in weighted) / total
        keyboard_used = (
            self.settings.risk.keyboard_weight / total if score.keyboard.available else 0.0
        )
        mouse_used = self.settings.risk.mouse_weight / total if score.mouse.available else 0.0
        return fused, keyboard_used, mouse_used

    def _candidate_level(self) -> tuple[RiskLevel, int]:
        medium_count = sum(value >= self.settings.risk.medium_threshold for value in self._history)
        high_count = sum(value >= self.settings.risk.high_threshold for value in self._history)
        if high_count >= self.settings.risk.breach_k:
            return RiskLevel.HIGH, high_count
        if medium_count >= self.settings.risk.breach_k:
            return RiskLevel.MEDIUM, high_count
        return RiskLevel.LOW, high_count

    def _with_hysteresis(self, candidate: RiskLevel) -> RiskLevel:
        current = self._risk_level
        if _RANK[candidate] >= _RANK[current]:
            return candidate
        if len(self._history) < self.settings.risk.breach_n:
            return current
        boundary = (
            self.settings.risk.high_threshold
            if current == RiskLevel.HIGH
            else self.settings.risk.medium_threshold
        )
        if all(value < boundary for value in self._history):
            return candidate
        return current

    def _failure_outcome(self, request: DecisionInput, detail: str) -> RiskOutcome:
        self.state_machine.degrade("RISK_INPUT_UNAVAILABLE")
        alert = RiskAlert(
            alert_type=AlertType.AVAILABILITY,
            severity="HIGH",
            code="RISK_INPUT_UNAVAILABLE",
            t_capture_us=request.t_decision_us,
            detail=detail,
        )
        self._emit_alert(alert)
        decision = RiskDecision(
            schema_version=PROTOCOL_VERSION,
            decision_id=self._id_factory(),
            user_id=self.user_id,
            session_id=request.session_id,
            segment_id=request.segment_id,
            window_id=request.score.window_id,
            t_decision_us=request.t_decision_us,
            quality_label=request.score.quality_label,
            fused_score=None,
            context_confidence=request.context.confidence,
            confidence_source=request.context.source,
            smoothed_score=None,
            risk_level=RiskLevel.UNAVAILABLE,
            user_state=UserState.DEGRADED,
            action=DecisionAction.NONE,
            reason_code="FAIL_OPEN_COMPONENT_UNAVAILABLE",
            threshold_config_version=self.settings.config_version,
            config_checksum=self.settings.config_checksum,
            shadow_mode=False,
            enforcement_applied=False,
        )
        if self._decision_sink is not None:
            self._decision_sink(decision)
        return RiskOutcome(decision, None, (alert,))

    def process(self, request: DecisionInput) -> RiskOutcome:
        if request.score.user_id != self.user_id:
            raise ValueError("score belongs to another user")
        if self._last_decision_us is not None and request.t_decision_us < self._last_decision_us:
            raise ValueError("decision timestamps must be monotonic")
        self._last_decision_us = request.t_decision_us

        state = self.state_machine.state
        if state in (UserState.ENROLLING, UserState.SUSPENDED):
            return RiskOutcome(None, None, ())
        if request.score.quality_label == WindowQuality.INSUFFICIENT_DATA:
            decision = RiskDecision(
                schema_version=PROTOCOL_VERSION,
                decision_id=self._id_factory(),
                user_id=self.user_id,
                session_id=request.session_id,
                segment_id=request.segment_id,
                window_id=request.score.window_id,
                t_decision_us=request.t_decision_us,
                quality_label=request.score.quality_label,
                fused_score=None,
                context_confidence=request.context.confidence,
                confidence_source=request.context.source,
                smoothed_score=None,
                risk_level=RiskLevel.UNAVAILABLE,
                user_state=state,
                action=DecisionAction.NONE,
                reason_code="INSUFFICIENT_EVIDENCE_HOLD",
                threshold_config_version=self.settings.config_version,
                config_checksum=self.settings.config_checksum,
                shadow_mode=state == UserState.CALIBRATING,
                enforcement_applied=False,
            )
            if self._decision_sink is not None:
                self._decision_sink(decision)
            return RiskOutcome(decision, None, ())

        failure = self._required_failure(request)
        if failure is not None:
            return self._failure_outcome(request, failure)
        try:
            fused, keyboard_used, mouse_used = self._fusion(request)
        except Exception as exc:
            return self._failure_outcome(request, f"{type(exc).__name__}: {exc}")

        adjusted = fused * request.context.confidence
        previous = self._smoothed
        alpha = self.settings.risk.ewma_alpha
        self._smoothed = adjusted if previous is None else alpha * adjusted + (1 - alpha) * previous
        self._history.append(self._smoothed)
        prior_level = self._risk_level
        candidate, high_count = self._candidate_level()
        self._risk_level = self._with_hysteresis(candidate)

        enforcement_enabled = state == UserState.ACTIVE
        policy = self._policy.decide(
            level=self._risk_level,
            high_count=high_count,
            t_decision_us=request.t_decision_us,
            enforcement_enabled=enforcement_enabled,
        )
        alerts: list[RiskAlert] = []
        if self._risk_level == RiskLevel.HIGH and prior_level != RiskLevel.HIGH:
            alerts.append(
                RiskAlert(
                    alert_type=AlertType.BEHAVIORAL,
                    severity="HIGH",
                    code="BEHAVIORAL_RISK_HIGH",
                    t_capture_us=request.t_decision_us,
                    detail="sustained behavioral evidence reached HIGH risk",
                )
            )
        if policy.budget_exhausted:
            alerts.append(
                RiskAlert(
                    alert_type=AlertType.BEHAVIORAL,
                    severity="HIGH",
                    code="ACTION_BUDGET_EXHAUSTED",
                    t_capture_us=request.t_decision_us,
                    detail="enforcement action suppressed; alert and monitoring continue",
                )
            )
        for alert in alerts:
            self._emit_alert(alert)

        reason = policy.reason_code
        if state == UserState.DEGRADED:
            emitted = DecisionAction.NONE
            applied = False
            reason = "DEGRADED_FAIL_OPEN"
        else:
            emitted = policy.emitted
            applied = policy.enforcement_applied
        decision = RiskDecision(
            schema_version=PROTOCOL_VERSION,
            decision_id=self._id_factory(),
            user_id=self.user_id,
            session_id=request.session_id,
            segment_id=request.segment_id,
            window_id=request.score.window_id,
            t_decision_us=request.t_decision_us,
            quality_label=request.score.quality_label,
            fused_score=fused,
            context_confidence=request.context.confidence,
            confidence_source=request.context.source,
            smoothed_score=self._smoothed,
            risk_level=self._risk_level,
            user_state=state,
            action=emitted,
            reason_code=reason,
            threshold_config_version=self.settings.config_version,
            config_checksum=self.settings.config_checksum,
            shadow_mode=state == UserState.CALIBRATING,
            enforcement_applied=applied,
        )
        trace = DecisionTrace(
            score=request.score,
            keyboard_weight_used=keyboard_used,
            mouse_weight_used=mouse_used,
            fused_score=fused,
            context_adjusted_score=adjusted,
            previous_smoothed_score=previous,
            smoothed_score=self._smoothed,
            breach_history=tuple(self._history),
            prior_risk_level=prior_level,
            resulting_risk_level=self._risk_level,
            requested_action=policy.requested,
            emitted_action=emitted,
            user_state=state,
            config_version=self.settings.config_version,
            config_checksum=self.settings.config_checksum,
        )
        if self._decision_sink is not None:
            self._decision_sink(decision)
        return RiskOutcome(decision, trace, tuple(alerts))

    def heartbeat_lost(self, *, t_capture_us: int, detail: str) -> RiskAlert:
        self.state_machine.degrade("COLLECTOR_HEARTBEAT_LOST")
        alert = RiskAlert(
            alert_type=AlertType.TAMPER,
            severity="HIGH",
            code="COLLECTOR_HEARTBEAT_LOST",
            t_capture_us=t_capture_us,
            detail=detail,
        )
        self._emit_alert(alert)
        return alert

    def component_failed(
        self,
        *,
        component: str,
        t_capture_us: int,
        detail: str,
    ) -> RiskAlert:
        if not component.strip() or not detail.strip():
            raise ValueError("component failure fields must not be blank")
        self.state_machine.degrade("BACKEND_COMPONENT_FAILED")
        alert = RiskAlert(
            alert_type=AlertType.AVAILABILITY,
            severity="HIGH",
            code="BACKEND_COMPONENT_FAILED",
            t_capture_us=t_capture_us,
            detail=f"{component}: {detail}",
        )
        self._emit_alert(alert)
        return alert

    def component_recovered(self) -> None:
        self.state_machine.recover()
