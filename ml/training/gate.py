"""Training-data admission boundary for development and promoted data.

Synthetic fixtures and approved public datasets may exercise the pipeline,
but team or pilot windows are accepted only when their segment has a C6
candidate proving every Model Update Manager gate passed and promotion was
audited. This keeps the ML package unusable as a bypass around T-018.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ml.features.schema import FeatureWindow, Provenance
from protocol.generated.python.contracts import CandidateDisposition, UpdateCandidate


class PromotionGateRequiredError(ValueError):
    """Raised when non-development training data lacks valid promotion evidence."""


def require_promotion_gate(
    user_id: str,
    windows: Sequence[FeatureWindow],
    promoted_candidates: Mapping[str, UpdateCandidate] | None = None,
) -> None:
    """Validate that every non-development segment was explicitly promoted."""

    development_provenance = {Provenance.SYNTHETIC, Provenance.PUBLIC}
    candidates = promoted_candidates or {}

    for window in windows:
        if window.user_id != user_id:
            raise PromotionGateRequiredError(
                f"training window {window.window_id!r} belongs to another user"
            )
        if window.provenance in development_provenance:
            continue

        candidate = candidates.get(window.segment_id)
        if candidate is None:
            raise PromotionGateRequiredError(
                f"segment {window.segment_id!r} has no Model Update Manager promotion evidence"
            )
        evidence = candidate.gate_evidence
        gates_passed = all(
            (
                evidence.g1_risk,
                evidence.g2_verification,
                evidence.g3_volume,
                evidence.g4_continuity,
                evidence.g5_quarantine,
                evidence.g6_schedule,
            )
        )
        if (
            candidate.user_id != user_id
            or candidate.segment_id != window.segment_id
            or candidate.disposition is not CandidateDisposition.PROMOTED
            or candidate.incident_recorded
            or not gates_passed
        ):
            raise PromotionGateRequiredError(
                f"segment {window.segment_id!r} does not have complete promotion evidence"
            )
