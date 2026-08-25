from __future__ import annotations

import pytest

from ml.evaluation.splitting import (
    DayOverlapError,
    assert_day_disjoint,
    day_disjoint_split,
    distinct_days,
    enrollment_length_subsets,
    leave_one_day_out_splits,
)
from ml.tests.conftest import generate_multiday_user_windows


def test_day_disjoint_split_separates_by_calendar_day(ml_config):
    windows = generate_multiday_user_windows("u1", 1, ml_config, num_days=4, segments_per_day=1, segment_minutes=10)
    days = distinct_days(windows)
    split = day_disjoint_split(windows, test_days=[days[-1]])
    assert_day_disjoint(split.train, split.test)  # must not raise
    assert set(w.collection_day for w in split.test) == {days[-1]}
    assert days[-1] not in split.train_days


def test_day_disjoint_split_rejects_unknown_day(ml_config):
    windows = generate_multiday_user_windows("u1", 1, ml_config, num_days=3, segments_per_day=1, segment_minutes=10)
    with pytest.raises(ValueError, match="not present"):
        day_disjoint_split(windows, test_days=["1999-01-01"])


def test_day_disjoint_split_rejects_covering_every_day(ml_config):
    windows = generate_multiday_user_windows("u1", 1, ml_config, num_days=2, segments_per_day=1, segment_minutes=10)
    days = distinct_days(windows)
    with pytest.raises(ValueError, match="every available day"):
        day_disjoint_split(windows, test_days=days)


def test_day_disjoint_split_rejects_empty_test_days(ml_config):
    windows = generate_multiday_user_windows("u1", 1, ml_config, num_days=2, segments_per_day=1, segment_minutes=10)
    with pytest.raises(ValueError, match="non-empty"):
        day_disjoint_split(windows, test_days=[])


def test_assert_day_disjoint_raises_on_overlap(ml_config):
    windows = generate_multiday_user_windows("u1", 1, ml_config, num_days=2, segments_per_day=1, segment_minutes=10)
    with pytest.raises(DayOverlapError):
        assert_day_disjoint(windows, windows)  # identical sets obviously overlap


def test_leave_one_day_out_yields_one_fold_per_day(ml_config):
    windows = generate_multiday_user_windows("u1", 1, ml_config, num_days=4, segments_per_day=1, segment_minutes=10)
    folds = list(leave_one_day_out_splits(windows))
    assert len(folds) == 4
    held_out_days = {f.test_days[0] for f in folds}
    assert held_out_days == set(distinct_days(windows))
    for f in folds:
        assert_day_disjoint(f.train, f.test)


def test_leave_one_day_out_requires_at_least_two_days(ml_config):
    windows = generate_multiday_user_windows("u1", 1, ml_config, num_days=1, segments_per_day=1, segment_minutes=10)
    with pytest.raises(ValueError, match=">= 2"):
        list(leave_one_day_out_splits(windows))


def test_enrollment_length_subsets_grow_with_day_count(ml_config):
    windows = generate_multiday_user_windows("u1", 1, ml_config, num_days=6, segments_per_day=1, segment_minutes=10)
    days = distinct_days(windows)
    holdout = [days[-1]]
    subsets = enrollment_length_subsets(windows, day_lengths=[1, 2, 3], holdout_days=holdout)
    assert len(subsets[1]) < len(subsets[2]) < len(subsets[3])
    for n_days, subset in subsets.items():
        assert all(w.collection_day != days[-1] for w in subset)  # holdout excluded


def test_enrollment_length_subsets_rejects_length_exceeding_available_days(ml_config):
    windows = generate_multiday_user_windows("u1", 1, ml_config, num_days=3, segments_per_day=1, segment_minutes=10)
    days = distinct_days(windows)
    with pytest.raises(ValueError, match="exceeds available"):
        enrollment_length_subsets(windows, day_lengths=[10], holdout_days=[days[-1]])
