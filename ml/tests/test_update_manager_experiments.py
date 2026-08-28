from __future__ import annotations

import numpy as np
import pytest

from ml.experiments.update_manager import (
    evaluate_drift_benefit,
    evaluate_poisoning_resistance,
)
from protocol.generated.python.contracts import UpdateCandidate


def _candidate(segment: str, disposition: str) -> UpdateCandidate:
    return UpdateCandidate.model_validate(
        {
            "schema_version": "1.0.0",
            "candidate_id": f"candidate-{segment}",
            "user_id": "synthetic-user",
            "segment_id": segment,
            "gate_evidence": {
                "g1_risk": True,
                "g2_verification": True,
                "g3_volume": True,
                "g4_continuity": True,
                "g5_quarantine": True,
                "g6_schedule": True,
            },
            "verification_anchor": "A3_SCHEDULED_PROMPT",
            "quarantined_at": "2026-01-01T00:00:00Z",
            "incident_recorded": disposition == "INVALIDATED",
            "disposition": disposition,
            "reason_code": "SYNTHETIC_TEST",
            "audit_revision": 1,
        }
    )


def test_e1_reports_paired_drift_change_without_relabelling_accuracy() -> None:
    result = evaluate_drift_benefit(
        frozen_genuine_risk=np.array([0.2, 0.8, 0.9, 0.1]),
        frozen_impostor_risk=np.array([0.8, 0.9, 0.2]),
        updated_genuine_risk=np.array([0.2, 0.3, 0.8, 0.1]),
        updated_impostor_risk=np.array([0.8, 0.9, 0.2]),
        threshold=0.75,
        bootstrap_resamples=100,
        bootstrap_confidence=0.95,
        random_seed=7,
    )
    assert result.updated.false_rejection_rate < result.frozen.false_rejection_rate
    assert result.false_acceptance_change == 0


def test_e2_blocks_labelled_poison_and_fails_if_promoted() -> None:
    result = evaluate_poisoning_resistance(
        [_candidate("poison", "INVALIDATED"), _candidate("clean", "PROMOTED")],
        injected_segment_ids={"poison"},
    )
    assert result.poisoning_block_rate == 1
    with pytest.raises(AssertionError, match="injected segment"):
        evaluate_poisoning_resistance(
            [_candidate("poison", "PROMOTED")],
            injected_segment_ids={"poison"},
        )
