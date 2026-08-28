"""Numbered, reproducible experiment entry points."""

from .update_manager import (
    DriftBenefitResult,
    PoisoningResistanceResult,
    evaluate_drift_benefit,
    evaluate_poisoning_resistance,
)

__all__ = [
    "DriftBenefitResult",
    "PoisoningResistanceResult",
    "evaluate_drift_benefit",
    "evaluate_poisoning_resistance",
]
