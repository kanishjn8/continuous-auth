"""Typed input, trace, and alert values around authoritative C3/C4."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from protocol.generated.python.contracts import (
    AlertType,
    ConfidenceSource,
    DecisionAction,
    RiskDecision,
    RiskLevel,
    ScoreResult,
    UserState,
)


@dataclass(frozen=True)
class ContextAssessment:
    confidence: float
    source: ConfidenceSource

    def __post_init__(self) -> None:
        if not 0 < self.confidence <= 1:
            raise ValueError("context confidence must be in (0, 1]")


@dataclass(frozen=True)
class DecisionInput:
    score: ScoreResult
    session_id: str
    segment_id: str
    t_decision_us: int
    context: ContextAssessment

    def __post_init__(self) -> None:
        if not self.session_id.strip() or not self.segment_id.strip():
            raise ValueError("decision session and segment identifiers must not be blank")
        if self.t_decision_us < 0:
            raise ValueError("t_decision_us must be non-negative")


Severity = Literal["LOW", "MEDIUM", "HIGH"]


@dataclass(frozen=True)
class RiskAlert:
    alert_type: AlertType
    severity: Severity
    code: str
    t_capture_us: int
    detail: str


@dataclass(frozen=True)
class DecisionTrace:
    score: ScoreResult
    keyboard_weight_used: float
    mouse_weight_used: float
    fused_score: float | None
    context_adjusted_score: float | None
    previous_smoothed_score: float | None
    smoothed_score: float | None
    breach_history: tuple[float, ...]
    prior_risk_level: RiskLevel
    resulting_risk_level: RiskLevel
    requested_action: DecisionAction
    emitted_action: DecisionAction
    user_state: UserState
    config_version: str
    config_checksum: str


@dataclass(frozen=True)
class RiskOutcome:
    decision: RiskDecision | None
    trace: DecisionTrace | None
    alerts: tuple[RiskAlert, ...]
