"""Typed input, trace, and alert values around authoritative C3/C4."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from protocol.generated.python.contracts import (
    AlertType,
    ConfidenceSource,
    ContextAdjustmentMode,
    DecisionAction,
    RiskDecision,
    RiskLevel,
    ScoreResult,
    UserState,
)


@dataclass(frozen=True)
class ContextAssessment:
    """Confidence plus the adjustment to apply to an already-fused score.

    ``MULTIPLICATIVE_DAMPING`` scales the fused score by ``confidence``.
    ``PER_CONTEXT_NORMALIZATION`` instead maps the score off the genuine
    distribution observed in this context and onto the user profile-wide
    genuine distribution, which removes a context-specific offset and spread
    without granting a blanket risk discount. The normalisation parameters are
    optional: when the layer has not yet observed enough genuine evidence it
    emits none and the damping path applies, which is always well defined.
    """

    confidence: float
    source: ConfidenceSource
    mode: ContextAdjustmentMode = ContextAdjustmentMode.MULTIPLICATIVE_DAMPING
    context_location: float | None = None
    context_scale: float | None = None
    baseline_location: float | None = None
    baseline_scale: float | None = None

    def __post_init__(self) -> None:
        if not 0 < self.confidence <= 1:
            raise ValueError("context confidence must be in (0, 1]")
        for name in ("context_scale", "baseline_scale"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when supplied")

    @property
    def normalization_available(self) -> bool:
        return (
            self.mode == ContextAdjustmentMode.PER_CONTEXT_NORMALIZATION
            and self.context_location is not None
            and self.context_scale is not None
            and self.baseline_location is not None
            and self.baseline_scale is not None
        )

    def adjust(self, fused: float) -> float:
        """Apply the configured context adjustment, bounded to [0, 1].

        The result feeds ``smoothed_score``, whose contract bounds it to
        [0, 1], so normalisation is clamped rather than allowed to escape the
        contract range.
        """

        if not self.normalization_available:
            return fused * self.confidence
        assert self.context_location is not None
        assert self.context_scale is not None
        assert self.baseline_location is not None
        assert self.baseline_scale is not None
        standardized = (fused - self.context_location) / self.context_scale
        adjusted = self.baseline_location + standardized * self.baseline_scale
        return min(1.0, max(0.0, adjusted))


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
