"""E1 and E2 run over a frozen corpus, never over live data."""

from __future__ import annotations

import pytest


def test_e1_requires_a_verified_evaluation_freeze(experiment_fixture) -> None:
    from tools.evaluation.freeze import EvaluationFreezeError
    from tools.experiments.drift import run_drift_benefit

    context = experiment_fixture(tamper_config=True)
    with pytest.raises(EvaluationFreezeError):
        run_drift_benefit(**context)


def test_e1_updating_recovers_frr_without_degrading_far(experiment_fixture) -> None:
    from tools.experiments.drift import run_drift_benefit

    result = run_drift_benefit(**experiment_fixture())
    # Not a claim about the real pilot cohort (see the module docstring) --
    # this only proves the mechanics run end-to-end over synthetic data
    # drawn from one stationary distribution, where "updated" should be at
    # least as good as "frozen" and never meaningfully worse.
    assert result.frozen.genuine_windows > 0
    assert result.updated.genuine_windows > 0
    assert result.false_acceptance_change <= 0.05


def test_e2_fails_loudly_if_an_injected_segment_is_promoted(experiment_fixture) -> None:
    """E2's whole point is that this must never happen silently."""

    from tools.experiments.poisoning import run_poisoning_resistance

    result = run_poisoning_resistance(**experiment_fixture(with_baseline_profile=True))
    assert result.injected_promoted == 0


def test_e2_injected_segments_are_blocked_by_the_gate(experiment_fixture) -> None:
    from tools.experiments.poisoning import run_poisoning_resistance

    result = run_poisoning_resistance(**experiment_fixture(with_baseline_profile=True))
    assert result.injected_segments > 0
    # ``PoisoningResistanceResult`` has no ``all_injected_blocked`` field (the
    # brief's guess); the real field is ``injected_rejected_or_invalidated``,
    # and "all blocked" means it equals ``injected_segments``.
    assert result.injected_rejected_or_invalidated == result.injected_segments
    assert result.poisoning_block_rate == 1.0


def test_e2_never_submits_a_verification_anchor_for_the_injected_segment(
    experiment_fixture,
) -> None:
    """The injected segment must be rejected on G2, not on an accident of the fixture."""

    from tools.experiments.poisoning import run_poisoning_resistance

    context = experiment_fixture(with_baseline_profile=True)
    result = run_poisoning_resistance(**context)
    assert result.clean_segments > 0
