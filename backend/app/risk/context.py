"""Per-application context confidence and score adjustment.

Design boundary (``docs/architecture.md``): context affects only *confidence*
and the *adjustment* applied to an already-fused score. It never enters an
identity-model feature array, and it never authenticates by itself.

Two properties distinguish this from the original T-012 layer:

1. **Empirical statistics are keyed on the opaque ``app_id``, not on the
   category taxonomy.** The category map (``config/app_categories.yaml``) is a
   cold-start *prior* only. An empty map is a fully supported configuration:
   every application starts at its category bootstrap weight and then learns
   from that user's own genuine scores. This is what keeps the system OS-wide
   -- a brand-new application installed tomorrow needs no map entry, no
   integration, and no model, and it still adapts.

2. **``UNKNOWN`` learns like any other category.** Pinning ``UNKNOWN`` to a
   static neutral weight made every unmapped application permanently
   non-adaptive which -- with an empty category map -- disabled the whole
   layer.

Confidence for a window is the focus-fraction-weighted mixture of its
applications' confidences, so a window spanning a switch is not attributed
wholly to whichever application happened to dominate it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from protocol.generated.python.contracts import (
    AppFocusShare,
    ApplicationCategory,
    ConfidenceSource,
    ContextAdjustmentMode,
    ContextConfig,
)

from .types import ContextAssessment

#: A window is reported as empirically driven only when applications covering
#: at least this share of it have enough observations, so an audit can tell
#: which regime produced a decision.
_EMPIRICAL_COVERAGE_MAJORITY = 0.5


@dataclass(frozen=True)
class EmpiricalStatistics:
    """Frequency-weighted running moments (West's weighted Welford)."""

    weight: float
    mean: float
    m2: float

    @property
    def variance(self) -> float:
        return self.m2 / self.weight if self.weight > 0 else 0.0

    @property
    def standard_deviation(self) -> float:
        return math.sqrt(max(self.variance, 0.0))

    def update(self, value: float, weight: float) -> EmpiricalStatistics:
        total = self.weight + weight
        if total <= 0:
            return self
        delta = value - self.mean
        mean = self.mean + (weight / total) * delta
        return EmpiricalStatistics(total, mean, self.m2 + weight * delta * (value - mean))


@dataclass(frozen=True)
class ConfidenceTrace:
    """Auditable explanation of one context assessment."""

    user_id: str
    assessment: ContextAssessment
    dominant_category: ApplicationCategory
    observed_evidence: float
    empirical_coverage: float
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
        self._statistics: dict[tuple[str, int], EmpiricalStatistics] = {}
        self._baselines: dict[str, EmpiricalStatistics] = {}
        self._learning_suspended = False

    @property
    def min_scale(self) -> float:
        return self.config.normalization_min_scale

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------
    def suspend_learning(self, enabled: bool) -> None:
        """Stop accumulating genuine statistics without affecting assessment.

        Set for the lifetime of a declared attacker drill (ADR-014).
        ``assess()`` keeps working from whatever was learned during genuine
        operation, which is exactly the right comparison baseline for a
        drill; what must not happen is the attacker's own behaviour becoming
        the new "genuine" reference part-way through the measurement.

        These statistics are in-memory only and are never persisted, so this
        is measurement isolation rather than model-contamination protection.
        The contamination protection is the corpus exclusion in
        ``backend/app/storage/drill.py``.
        """

        self._learning_suspended = enabled

    def learning_suspended(self) -> bool:
        return self._learning_suspended

    def observe_genuine(
        self,
        *,
        user_id: str,
        shares: Sequence[AppFocusShare],
        risk_score: float,
    ) -> None:
        """Attribute one genuine (LOW-risk) score across the window's applications.

        Attribution is proportional to focus share, so a window split between
        two applications contributes partial evidence to each rather than full
        evidence to whichever one dominated.

        A no-op while learning is suspended (see :meth:`suspend_learning`).
        """

        if self._learning_suspended:
            return
        if not user_id.strip():
            raise ValueError("context user_id must not be blank")
        if not math.isfinite(risk_score) or not 0 <= risk_score <= 1:
            raise ValueError("genuine risk score must be finite and in [0, 1]")

        for share in shares:
            if share.fraction <= 0:
                continue
            key = (user_id, share.app_id)
            current = self._statistics.get(key, EmpiricalStatistics(0.0, 0.0, 0.0))
            self._statistics[key] = current.update(risk_score, share.fraction)

        baseline = self._baselines.get(user_id, EmpiricalStatistics(0.0, 0.0, 0.0))
        self._baselines[user_id] = baseline.update(risk_score, 1.0)

    # ------------------------------------------------------------------
    # Assessment
    # ------------------------------------------------------------------
    def _application_confidence(
        self, user_id: str, share: AppFocusShare
    ) -> tuple[float, bool, EmpiricalStatistics | None]:
        """Confidence for one application, and whether it is empirically derived."""

        bootstrap = self.config.bootstrap_weights[share.category.value]
        statistics = self._statistics.get((user_id, share.app_id))
        if statistics is not None and statistics.weight >= self.config.min_empirical_observations:
            # Scores are bounded in [0,1], whose maximum population variance is
            # 0.25; variance_scale normalises that bound onto [0,1] confidence.
            empirical = 1.0 - self.config.variance_scale * statistics.variance
            return max(self.config.confidence_floor, empirical), True, statistics
        return max(self.config.confidence_floor, bootstrap), False, statistics

    def assess(
        self,
        *,
        user_id: str,
        shares: Sequence[AppFocusShare],
        dominant_category: ApplicationCategory,
    ) -> ConfidenceTrace:
        if not user_id.strip():
            raise ValueError("context user_id must not be blank")

        usable = [share for share in shares if share.fraction > 0]
        total_fraction = sum(share.fraction for share in usable)
        bootstrap_reference = self.config.bootstrap_weights[dominant_category.value]

        if not usable or total_fraction <= 0:
            # No attributable focus at all: neutral, but never zero (G06).
            confidence = max(self.config.confidence_floor, bootstrap_reference)
            assessment = ContextAssessment(confidence, ConfidenceSource.NEUTRAL_FALLBACK)
            return ConfidenceTrace(
                user_id, assessment, dominant_category, 0.0, 0.0, None, bootstrap_reference
            )

        blended = 0.0
        empirical_coverage = 0.0
        observed_evidence = 0.0
        context_mean = 0.0
        context_variance = 0.0
        any_statistics = False

        for share in usable:
            confidence, is_empirical, statistics = self._application_confidence(user_id, share)
            normalized = share.fraction / total_fraction
            blended += normalized * confidence
            if is_empirical and statistics is not None:
                empirical_coverage += normalized
                context_mean += normalized * statistics.mean
                context_variance += normalized * statistics.variance
            if statistics is not None:
                any_statistics = True
                observed_evidence += statistics.weight

        confidence = min(1.0, max(self.config.confidence_floor, blended))

        if empirical_coverage >= _EMPIRICAL_COVERAGE_MAJORITY:
            source = ConfidenceSource.EMPIRICAL
        elif any_statistics or dominant_category != ApplicationCategory.UNKNOWN:
            source = ConfidenceSource.BOOTSTRAP
        else:
            source = ConfidenceSource.NEUTRAL_FALLBACK

        assessment = self._build_assessment(
            user_id=user_id,
            confidence=confidence,
            source=source,
            empirical_coverage=empirical_coverage,
            context_mean=context_mean,
            context_variance=context_variance,
        )
        return ConfidenceTrace(
            user_id,
            assessment,
            dominant_category,
            observed_evidence,
            empirical_coverage,
            context_variance if empirical_coverage > 0 else None,
            bootstrap_reference,
        )

    def _build_assessment(
        self,
        *,
        user_id: str,
        confidence: float,
        source: ConfidenceSource,
        empirical_coverage: float,
        context_mean: float,
        context_variance: float,
    ) -> ContextAssessment:
        """Attach normalisation parameters when the configured mode needs them.

        Normalisation requires both a per-context and a whole-profile genuine
        distribution. Until both exist the assessment falls back to damping,
        which is always well defined.
        """

        if self.config.adjustment_mode != ContextAdjustmentMode.PER_CONTEXT_NORMALIZATION:
            return ContextAssessment(confidence, source)
        baseline = self._baselines.get(user_id)
        if (
            baseline is None
            or empirical_coverage < _EMPIRICAL_COVERAGE_MAJORITY
            or baseline.weight < self.config.min_empirical_observations
        ):
            return ContextAssessment(confidence, source)
        return ContextAssessment(
            confidence,
            source,
            mode=ContextAdjustmentMode.PER_CONTEXT_NORMALIZATION,
            context_location=context_mean,
            context_scale=max(math.sqrt(max(context_variance, 0.0)), self.min_scale),
            baseline_location=baseline.mean,
            baseline_scale=max(baseline.standard_deviation, self.min_scale),
        )

    def statistics(self) -> dict[tuple[str, int], EmpiricalStatistics]:
        return dict(self._statistics)

    def baselines(self) -> dict[str, EmpiricalStatistics]:
        return dict(self._baselines)
