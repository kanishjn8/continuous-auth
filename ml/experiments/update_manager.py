"""E1 drift-benefit and E2 poisoning-resistance experiment calculations."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from protocol.generated.python.contracts import CandidateDisposition, UpdateCandidate


@dataclass(frozen=True)
class OperatingRates:
    false_rejection_rate: float
    false_acceptance_rate: float
    genuine_windows: int
    impostor_windows: int


@dataclass(frozen=True)
class DriftBenefitResult:
    frozen: OperatingRates
    updated: OperatingRates
    false_rejection_change: float
    false_acceptance_change: float
    false_rejection_change_ci: tuple[float, float]


def _rates(genuine_risk: np.ndarray, impostor_risk: np.ndarray, threshold: float) -> OperatingRates:
    genuine = np.asarray(genuine_risk, dtype=float)
    impostor = np.asarray(impostor_risk, dtype=float)
    if len(genuine) == 0 or len(impostor) == 0:
        raise ValueError("E1 requires genuine and impostor windows in both arms")
    if not 0 < threshold < 1:
        raise ValueError("risk threshold must be in (0, 1)")
    if not np.all(np.isfinite(genuine)) or not np.all(np.isfinite(impostor)):
        raise ValueError("E1 risk scores must be finite")
    return OperatingRates(
        false_rejection_rate=float(np.mean(genuine >= threshold)),
        false_acceptance_rate=float(np.mean(impostor < threshold)),
        genuine_windows=len(genuine),
        impostor_windows=len(impostor),
    )


def evaluate_drift_benefit(
    *,
    frozen_genuine_risk: np.ndarray,
    frozen_impostor_risk: np.ndarray,
    updated_genuine_risk: np.ndarray,
    updated_impostor_risk: np.ndarray,
    threshold: float,
    bootstrap_resamples: int,
    bootstrap_confidence: float,
    random_seed: int,
) -> DriftBenefitResult:
    """Compare frozen/updated profiles on identical paired evaluation windows."""

    frozen_genuine = np.asarray(frozen_genuine_risk, dtype=float)
    updated_genuine = np.asarray(updated_genuine_risk, dtype=float)
    if len(frozen_genuine) != len(updated_genuine):
        raise ValueError("E1 genuine arms must contain the same paired windows")
    if bootstrap_resamples < 1 or not 0 < bootstrap_confidence < 1:
        raise ValueError("bootstrap configuration is invalid")
    frozen = _rates(frozen_genuine, frozen_impostor_risk, threshold)
    updated = _rates(updated_genuine, updated_impostor_risk, threshold)
    paired = (updated_genuine >= threshold).astype(float) - (frozen_genuine >= threshold).astype(
        float
    )
    random = np.random.default_rng(random_seed)
    means = np.empty(bootstrap_resamples)
    for index in range(bootstrap_resamples):
        sample = random.integers(0, len(paired), size=len(paired))
        means[index] = float(np.mean(paired[sample]))
    tail = (1 - bootstrap_confidence) / 2
    return DriftBenefitResult(
        frozen=frozen,
        updated=updated,
        false_rejection_change=updated.false_rejection_rate - frozen.false_rejection_rate,
        false_acceptance_change=updated.false_acceptance_rate - frozen.false_acceptance_rate,
        false_rejection_change_ci=(
            float(np.quantile(means, tail)),
            float(np.quantile(means, 1 - tail)),
        ),
    )


@dataclass(frozen=True)
class PoisoningResistanceResult:
    injected_segments: int
    injected_rejected_or_invalidated: int
    injected_promoted: int
    clean_segments: int
    clean_promoted: int

    @property
    def poisoning_block_rate(self) -> float:
        if self.injected_segments == 0:
            raise ValueError("E2 requires at least one injected segment")
        return self.injected_rejected_or_invalidated / self.injected_segments


def evaluate_poisoning_resistance(
    candidates: list[UpdateCandidate],
    *,
    injected_segment_ids: set[str],
) -> PoisoningResistanceResult:
    """E2 fails explicitly if any labelled poisoned segment reaches promotion."""

    if not injected_segment_ids:
        raise ValueError("E2 requires labelled injected segments")
    observed = {candidate.segment_id for candidate in candidates}
    missing = injected_segment_ids - observed
    if missing:
        raise ValueError("E2 candidate results omit injected segments")
    injected = [
        candidate for candidate in candidates if candidate.segment_id in injected_segment_ids
    ]
    clean = [
        candidate for candidate in candidates if candidate.segment_id not in injected_segment_ids
    ]
    terminal_block = {CandidateDisposition.REJECTED, CandidateDisposition.INVALIDATED}
    result = PoisoningResistanceResult(
        injected_segments=len(injected),
        injected_rejected_or_invalidated=sum(
            candidate.disposition in terminal_block for candidate in injected
        ),
        injected_promoted=sum(
            candidate.disposition is CandidateDisposition.PROMOTED for candidate in injected
        ),
        clean_segments=len(clean),
        clean_promoted=sum(
            candidate.disposition is CandidateDisposition.PROMOTED for candidate in clean
        ),
    )
    if result.injected_promoted:
        raise AssertionError("poisoning-resistance gate failed: an injected segment was promoted")
    return result
