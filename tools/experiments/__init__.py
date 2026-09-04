"""E1 (drift-benefit) and E2 (poisoning-resistance) experiment drivers.

PLAN.md Section 12.4; design doc Section 8.6. See ``tools/experiments/drift.py``
and ``tools/experiments/poisoning.py`` for the integrity requirements each
driver's results are subject to before they may be treated as evidence.
"""

from __future__ import annotations

from .drift import run_drift_benefit
from .poisoning import run_poisoning_resistance

__all__ = ["run_drift_benefit", "run_poisoning_resistance"]
