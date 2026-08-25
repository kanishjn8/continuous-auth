from __future__ import annotations

import numpy as np
import pytest

from ml.calibration.percentile import PercentileCalibrator


def test_fit_requires_nonempty_scores():
    with pytest.raises(ValueError):
        PercentileCalibrator.fit(np.array([]))


def test_percentile_bounds_are_0_to_100():
    calib = PercentileCalibrator.fit(np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
    below = calib.transform(np.array([-100.0]))[0]
    above = calib.transform(np.array([100.0]))[0]
    assert below == 0.0
    assert above == 100.0


def test_percentile_monotonic_in_raw_score():
    rng = np.random.default_rng(0)
    reference = rng.normal(size=200)
    calib = PercentileCalibrator.fit(reference)
    probe = np.sort(rng.normal(size=50))
    pcts = calib.transform(probe)
    assert np.all(np.diff(pcts) >= 0)


def test_median_of_reference_distribution_is_near_50th_percentile():
    reference = np.arange(1, 101, dtype=float)  # 1..100
    calib = PercentileCalibrator.fit(reference)
    pct = calib.transform(np.array([50.0]))[0]
    assert 45.0 <= pct <= 55.0


def test_transform_before_fit_raises():
    calib = PercentileCalibrator()
    with pytest.raises(ValueError):
        calib.transform(np.array([1.0]))
