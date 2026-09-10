from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from itertools import count
from pathlib import Path

import pytest

from backend.app.decisions import DecisionPolicy
from backend.app.risk import (
    ContextAssessment,
    DecisionInput,
    RiskAlert,
    RiskEngine,
    UserStateMachine,
    load_risk_settings,
)
from backend.app.risk.config import RiskConfigError, RiskSettings
from protocol.generated.python.contracts import (
    AlertType,
    ConfidenceSource,
    DecisionAction,
    ModalityScore,
    ModelStatus,
    RiskLevel,
    ScoreResult,
    UserState,
    WindowQuality,
)


def _score(
    *,
    value: float | None,
    quality: WindowQuality = WindowQuality.FULL,
    window_index: int = 0,
    keyboard_status: ModelStatus = ModelStatus.SCORED,
    mouse_status: ModelStatus = ModelStatus.SCORED,
) -> ScoreResult:
    keyboard_available = value is not None and quality in (
        WindowQuality.FULL,
        WindowQuality.KBD_ONLY,
    )
    mouse_available = value is not None and quality in (
        WindowQuality.FULL,
        WindowQuality.MOUSE_ONLY,
    )
    return ScoreResult(
        schema_version="1.0.0",
        user_id="synthetic-user",
        profile_version="profile-v1",
        feature_schema_version="1.0.0",
        model_version="model-v1",
        window_id=f"window-{window_index}",
        quality_label=quality,
        keyboard=ModalityScore(
            available=keyboard_available,
            raw_score=value if keyboard_available else None,
            calibrated_score=value if keyboard_available else None,
            status=keyboard_status if keyboard_available else ModelStatus.UNAVAILABLE,
        ),
        mouse=ModalityScore(
            available=mouse_available,
            raw_score=value if mouse_available else None,
            calibrated_score=value if mouse_available else None,
            status=mouse_status if mouse_available else ModelStatus.UNAVAILABLE,
        ),
    )


def _request(
    value: float | None,
    index: int,
    *,
    quality: WindowQuality = WindowQuality.FULL,
    confidence: float = 1.0,
    keyboard_status: ModelStatus = ModelStatus.SCORED,
) -> DecisionInput:
    return DecisionInput(
        score=_score(
            value=value,
            quality=quality,
            window_index=index,
            keyboard_status=keyboard_status,
        ),
        session_id="session-1",
        segment_id="segment-1",
        t_decision_us=index * 61_000_000,
        context=ContextAssessment(confidence, ConfidenceSource.NEUTRAL_FALLBACK),
    )


def _ids() -> Callable[[], str]:
    sequence = count(1)
    return lambda: f"decision-{next(sequence)}"


def _active_engine(settings: RiskSettings | None = None) -> RiskEngine:
    resolved = settings or load_risk_settings()
    state = UserStateMachine(resolved.enrollment, initial=UserState.ACTIVE)
    return RiskEngine(
        user_id="synthetic-user",
        settings=resolved,
        state_machine=state,
        id_factory=_ids(),
    )


def _immediate_smoothing(settings: RiskSettings) -> RiskSettings:
    return replace(
        settings,
        risk=settings.risk.model_copy(update={"ewma_alpha": 1.0}),
    )


def test_config_is_strict_cross_validated_and_versioned(tmp_path: Path) -> None:
    settings = load_risk_settings()
    assert len(settings.config_checksum) == 64
    assert settings.risk.medium_threshold < settings.risk.high_threshold

    # The deployed pilot operating point (docs/threshold-pair-analysis-
    # 2026-09-10.md, candidate pair #5), selected by human/team review after
    # diagnosis.md showed 0.45/0.75 rejected ~55%/~19% of the enrolled
    # user's own in-sample training windows. Asserted against the literal
    # values, not just the ordering invariant, so a future edit to
    # config/risk.development.yaml that silently reverts or drifts the
    # operating point is caught here.
    assert settings.risk.medium_threshold == pytest.approx(0.80)
    assert settings.risk.high_threshold == pytest.approx(0.90)

    invalid = tmp_path / "risk.yaml"
    invalid.write_text(
        "config_version: bad\nprotocol_version: 1.0.0\ndevelopment_only: true\n"
        "enrollment: {min_windows: 1, min_distinct_days: 1, calibration_windows: 1}\n"
        "risk: {keyboard_weight: 0.5, mouse_weight: 0.5, ewma_alpha: 0.5, "
        "medium_threshold: 0.8, high_threshold: 0.2, breach_k: 2, breach_n: 3, "
        "cooldown_seconds: 1, action_budget: 1}\n",
        encoding="utf-8",
    )
    with pytest.raises(RiskConfigError, match="medium_threshold"):
        load_risk_settings(invalid)


def test_availability_weighted_fusion_and_context_trace() -> None:
    engine = _active_engine(_immediate_smoothing(load_risk_settings()))
    outcome = engine.process(_request(0.8, 1, quality=WindowQuality.KBD_ONLY, confidence=0.5))
    assert outcome.decision is not None and outcome.trace is not None
    assert outcome.decision.fused_score == pytest.approx(0.8)
    assert outcome.decision.smoothed_score == pytest.approx(0.4)
    assert outcome.trace.keyboard_weight_used == pytest.approx(1.0)
    assert outcome.trace.mouse_weight_used == pytest.approx(0.0)
    assert outcome.trace.context_adjusted_score == pytest.approx(0.4)


def test_single_anomalous_window_never_enforces() -> None:
    engine = _active_engine(_immediate_smoothing(load_risk_settings()))
    outcome = engine.process(_request(1.0, 1))
    assert outcome.decision is not None
    assert outcome.decision.risk_level == RiskLevel.LOW
    assert outcome.decision.action == DecisionAction.CONTINUE
    assert outcome.decision.enforcement_applied is False


def test_sustained_anomaly_escalates_then_terminates() -> None:
    engine = _active_engine(_immediate_smoothing(load_risk_settings()))
    decisions = [engine.process(_request(1.0, index)).decision for index in range(1, 6)]
    present = [decision for decision in decisions if decision is not None]
    assert len(present) == 5
    assert present[1].risk_level == RiskLevel.LOW
    assert present[2].action == DecisionAction.REAUTH
    assert present[2].enforcement_applied is True
    assert present[4].action == DecisionAction.TERMINATE
    assert present[4].enforcement_applied is True


def test_insufficient_data_holds_ewma_history_and_breach_counter() -> None:
    engine = _active_engine(_immediate_smoothing(load_risk_settings()))
    first = engine.process(_request(1.0, 1))
    second = engine.process(_request(1.0, 2))
    held = engine.process(_request(None, 3, quality=WindowQuality.INSUFFICIENT_DATA))
    third = engine.process(_request(1.0, 4))
    assert first.trace is not None and second.trace is not None and third.trace is not None
    assert held.decision is not None and held.decision.smoothed_score is None
    assert held.decision.action == DecisionAction.NONE
    assert len(second.trace.breach_history) == 2
    assert len(third.trace.breach_history) == 3
    assert third.decision is not None and third.decision.risk_level == RiskLevel.HIGH


def test_hysteresis_requires_full_recovery_history() -> None:
    settings = _immediate_smoothing(load_risk_settings())
    engine = _active_engine(settings)
    for index in range(1, 4):
        engine.process(_request(1.0, index))
    one_normal = engine.process(_request(0.0, 4))
    assert one_normal.decision is not None
    assert one_normal.decision.risk_level == RiskLevel.HIGH
    recovered = None
    for index in range(5, 9):
        recovered = engine.process(_request(0.0, index))
    assert recovered is not None and recovered.decision is not None
    assert recovered.decision.risk_level == RiskLevel.LOW


def test_enrollment_calibration_shadow_and_active_transitions() -> None:
    settings = load_risk_settings()
    state = UserStateMachine(settings.enrollment)
    engine = RiskEngine(
        user_id="synthetic-user",
        settings=settings,
        state_machine=state,
        id_factory=_ids(),
    )
    assert engine.process(_request(0.1, 1)).decision is None
    transition = state.observe_progress(
        enrollment_windows=settings.enrollment.min_windows,
        distinct_days=settings.enrollment.min_distinct_days,
        calibration_windows=0,
    )
    assert transition is not None and transition.current == UserState.CALIBRATING
    shadow = engine.process(_request(1.0, 2))
    assert shadow.decision is not None
    assert shadow.decision.shadow_mode is True
    assert shadow.decision.enforcement_applied is False
    state.observe_progress(
        enrollment_windows=settings.enrollment.min_windows,
        distinct_days=settings.enrollment.min_distinct_days,
        calibration_windows=settings.enrollment.calibration_windows,
    )
    assert state.state == UserState.ACTIVE
    engine.set_shadow_mode(True)
    administrative_shadow = engine.process(_request(1.0, 3))
    assert administrative_shadow.decision is not None
    assert administrative_shadow.decision.shadow_mode is True
    assert administrative_shadow.decision.enforcement_applied is False


def test_model_failure_is_fail_open_loud_and_degraded() -> None:
    alerts: list[RiskAlert] = []
    settings = load_risk_settings()
    state = UserStateMachine(settings.enrollment, initial=UserState.ACTIVE)
    engine = RiskEngine(
        user_id="synthetic-user",
        settings=settings,
        state_machine=state,
        alert_sink=alerts.append,
        id_factory=_ids(),
    )
    request = _request(0.9, 1)
    failed_keyboard = request.score.keyboard.model_copy(
        update={
            "available": False,
            "raw_score": None,
            "calibrated_score": None,
            "status": ModelStatus.FAILED,
        }
    )
    failed_score = request.score.model_copy(update={"keyboard": failed_keyboard})
    outcome = engine.process(replace(request, score=failed_score))
    assert outcome.decision is not None
    assert outcome.decision.user_state == UserState.DEGRADED
    assert outcome.decision.action == DecisionAction.NONE
    assert outcome.decision.enforcement_applied is False
    assert alerts[0].alert_type == AlertType.AVAILABILITY
    assert alerts[0].severity == "HIGH"


def test_heartbeat_loss_is_tamper_but_never_terminates() -> None:
    engine = _active_engine()
    alert = engine.heartbeat_lost(t_capture_us=1, detail="synthetic heartbeat timeout")
    assert alert.alert_type == AlertType.TAMPER
    assert engine.state_machine.state == UserState.DEGRADED


def test_backend_failure_is_availability_fail_open_and_can_recover() -> None:
    engine = _active_engine()
    alert = engine.component_failed(
        component="feature-extractor",
        t_capture_us=1,
        detail="synthetic unavailable",
    )
    assert alert.alert_type == AlertType.AVAILABILITY
    assert alert.severity == "HIGH"
    assert engine.state_machine.state == UserState.DEGRADED
    engine.component_recovered()
    recovered_state: UserState = engine.state_machine.state
    assert recovered_state == UserState.ACTIVE


def test_cooldown_and_action_budget_suppress_repeated_actions() -> None:
    settings = load_risk_settings()
    policy = DecisionPolicy(settings)
    first = policy.decide(
        level=RiskLevel.MEDIUM,
        high_count=0,
        t_decision_us=1,
        enforcement_enabled=True,
    )
    repeated = policy.decide(
        level=RiskLevel.MEDIUM,
        high_count=0,
        t_decision_us=2,
        enforcement_enabled=True,
    )
    second_kind = policy.decide(
        level=RiskLevel.HIGH,
        high_count=settings.risk.breach_k,
        t_decision_us=3,
        enforcement_enabled=True,
    )
    budget = policy.decide(
        level=RiskLevel.HIGH,
        high_count=settings.risk.breach_n,
        t_decision_us=4,
        enforcement_enabled=True,
    )
    assert first.enforcement_applied is True
    assert repeated.reason_code == "ACTION_COOLDOWN"
    assert second_kind.enforcement_applied is True
    assert budget.reason_code == "ACTION_BUDGET"
    assert budget.budget_exhausted is True


def test_replay_is_deterministic_and_timestamp_regression_rejected() -> None:
    settings = _immediate_smoothing(load_risk_settings())
    traces = []
    for _ in range(2):
        engine = _active_engine(settings)
        run = [
            engine.process(_request(value, index)) for index, value in enumerate((0.1, 0.9, 0.9), 1)
        ]
        traces.append(
            [(item.decision.model_dump(), item.trace) for item in run if item.decision is not None]
        )
    assert traces[0] == traces[1]

    engine = _active_engine(settings)
    engine.process(_request(0.1, 2))
    with pytest.raises(ValueError, match="monotonic"):
        engine.process(_request(0.1, 1))


def test_context_confidence_can_never_be_zero() -> None:
    with pytest.raises(ValueError, match="context confidence"):
        ContextAssessment(0.0, ConfidenceSource.NEUTRAL_FALLBACK)
