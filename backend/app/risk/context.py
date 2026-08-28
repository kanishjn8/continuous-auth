"""T-012 bootstrap and empirical context-confidence arbitration."""

from __future__ import annotations

import math
from dataclasses import dataclass

from protocol.generated.python.contracts import (
    ApplicationCategory,
    ConfidenceSource,
    ContextConfig,
)

from .types import ContextAssessment


@dataclass(frozen=True)
class EmpiricalStatistics:
    count: int
    mean: float
    m2: float

    @property
    def variance(self) -> float:
        return self.m2 / self.count if self.count else 0.0


@dataclass(frozen=True)
class ConfidenceTrace:
    user_id: str
    category: ApplicationCategory
    assessment: ContextAssessment
    observations: int
    genuine_score_variance: float | None
    bootstrap_confidence: float


class ContextConfidenceLayer:
    """Context changes confidence only; it never changes model identity features."""

    def __init__(self, config: ContextConfig):
        self.config = config
        allowed = {category.value for category in ApplicationCategory}
        unknown = set(config.bootstrap_weights) - allowed
        if unknown:
            raise ValueError(f"unknown bootstrap categories: {sorted(unknown)!r}")
        missing = allowed - set(config.bootstrap_weights)
        if missing:
            raise ValueError(f"missing bootstrap categories: {sorted(missing)!r}")
        if any(value < config.confidence_floor for value in config.bootstrap_weights.values()):
            raise ValueError("bootstrap confidence cannot be below confidence_floor")
        self._statistics: dict[tuple[str, ApplicationCategory], EmpiricalStatistics] = {}

    def observe_genuine(
        self,
        *,
        user_id: str,
        category: ApplicationCategory,
        risk_score: float,
    ) -> None:
        if not user_id.strip():
            raise ValueError("context user_id must not be blank")
        if not math.isfinite(risk_score) or not 0 <= risk_score <= 1:
            raise ValueError("genuine risk score must be finite and in [0, 1]")
        key = (user_id, category)
        current = self._statistics.get(key, EmpiricalStatistics(0, 0.0, 0.0))
        count = current.count + 1
        delta = risk_score - current.mean
        mean = current.mean + delta / count
        delta_after = risk_score - mean
        self._statistics[key] = EmpiricalStatistics(count, mean, current.m2 + delta * delta_after)

    def assess(self, *, user_id: str, category: ApplicationCategory) -> ConfidenceTrace:
        if not user_id.strip():
            raise ValueError("context user_id must not be blank")
        bootstrap = self.config.bootstrap_weights[category.value]
        statistics = self._statistics.get((user_id, category))
        if (
            category != ApplicationCategory.UNKNOWN
            and statistics is not None
            and statistics.count >= self.config.min_empirical_observations
        ):
            # Scores are bounded in [0,1], whose maximum population variance
            # is 0.25. Normalising by that bound maps variance to confidence.
            empirical = max(self.config.confidence_floor, 1.0 - 4.0 * statistics.variance)
            assessment = ContextAssessment(empirical, ConfidenceSource.EMPIRICAL)
            return ConfidenceTrace(
                user_id,
                category,
                assessment,
                statistics.count,
                statistics.variance,
                bootstrap,
            )
        source = (
            ConfidenceSource.NEUTRAL_FALLBACK
            if category == ApplicationCategory.UNKNOWN
            else ConfidenceSource.BOOTSTRAP
        )
        assessment = ContextAssessment(max(self.config.confidence_floor, bootstrap), source)
        return ConfidenceTrace(
            user_id,
            category,
            assessment,
            0 if statistics is None else statistics.count,
            None if statistics is None else statistics.variance,
            bootstrap,
        )

    def statistics(self) -> dict[tuple[str, ApplicationCategory], EmpiricalStatistics]:
        return dict(self._statistics)
