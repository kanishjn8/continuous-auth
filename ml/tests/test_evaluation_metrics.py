from __future__ import annotations

import numpy as np
import pytest

from ml.evaluation.metrics import (
    PerUserMetrics,
    bootstrap_confidence_interval,
    compute_eer,
    compute_far_frr,
    compute_roc_det_curve,
    per_user_metric_spread,
)


def test_far_frr_perfectly_separated_distributions():
    genuine = np.array([90.0, 92.0, 95.0, 99.0])
    impostor = np.array([5.0, 8.0, 10.0, 2.0])
    result = compute_far_frr(genuine, impostor, threshold=50.0)
    assert result.far == 0.0
    assert result.frr == 0.0


def test_far_frr_requires_nonempty_inputs():
    with pytest.raises(ValueError):
        compute_far_frr(np.array([]), np.array([1.0]), threshold=50.0)
    with pytest.raises(ValueError):
        compute_far_frr(np.array([1.0]), np.array([]), threshold=50.0)


def test_eer_near_zero_for_perfectly_separated_distributions():
    genuine = np.random.default_rng(0).uniform(80, 100, size=200)
    impostor = np.random.default_rng(1).uniform(0, 20, size=200)
    result = compute_eer(genuine, impostor)
    assert result.eer < 0.01


def test_eer_near_half_for_identical_distributions():
    rng = np.random.default_rng(0)
    shared = rng.normal(50, 15, size=500)
    genuine = shared[:250]
    impostor = shared[250:]
    result = compute_eer(genuine, impostor)
    assert 0.3 <= result.eer <= 0.6


def test_roc_curve_far_is_monotonically_nonincreasing_in_threshold():
    genuine = np.array([60.0, 70.0, 80.0])
    impostor = np.array([20.0, 30.0, 40.0])
    curve = compute_roc_det_curve(genuine, impostor, n_thresholds=101)
    assert np.all(np.diff(curve.far) <= 1e-12)


def test_roc_curve_frr_is_monotonically_nondecreasing_in_threshold():
    genuine = np.array([60.0, 70.0, 80.0])
    impostor = np.array([20.0, 30.0, 40.0])
    curve = compute_roc_det_curve(genuine, impostor, n_thresholds=101)
    assert np.all(np.diff(curve.frr) >= -1e-12)


def test_per_user_metric_spread_reports_distribution_not_just_mean():
    results = [
        PerUserMetrics(user_id="a", n_genuine=10, n_impostor=10, eer=0.1, eer_threshold=50.0),
        PerUserMetrics(user_id="b", n_genuine=10, n_impostor=10, eer=0.5, eer_threshold=50.0),
    ]
    spread = per_user_metric_spread(results)
    assert spread["mean"] == pytest.approx(0.3)
    assert spread["min"] == 0.1
    assert spread["max"] == 0.5
    assert spread["std"] > 0.0


def test_per_user_metric_spread_requires_nonempty():
    with pytest.raises(ValueError):
        per_user_metric_spread([])


def test_bootstrap_ci_bounds_are_ordered_and_contain_true_mean():
    rng = np.random.default_rng(42)
    values = rng.normal(10.0, 2.0, size=300)
    lo, hi = bootstrap_confidence_interval(values, n_resamples=500, seed=1)
    assert lo <= hi
    assert lo <= np.mean(values) <= hi


def test_bootstrap_ci_requires_nonempty():
    with pytest.raises(ValueError):
        bootstrap_confidence_interval(np.array([]))
