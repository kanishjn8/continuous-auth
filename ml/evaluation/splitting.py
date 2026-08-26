"""Day-disjoint / leave-one-day-out train/test splitting (PLAN.md Section 10.3).

"Random splitting of windows is prohibited for any reported result...
Train on data from one set of calendar days; evaluate on entirely different
days... Where data allows, use leave-one-day-out cross-validation."

Every function here operates on ``collection_day`` (a date-only string,
window metadata -- never a feature, per Section 7.3) and never inspects
``t_start_us``/``t_end_us`` for splitting, so no wall-clock/time-of-day
information can leak into the split boundary (ADR-010 adjacent concern).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Sequence

from ml.features.schema import FeatureWindow


class DayOverlapError(ValueError):
    """Raised when a proposed split has calendar days in both partitions."""


@dataclass(frozen=True)
class DaySplit:
    train: list[FeatureWindow]
    test: list[FeatureWindow]
    train_days: tuple[str, ...]
    test_days: tuple[str, ...]


def distinct_days(windows: Sequence[FeatureWindow]) -> list[str]:
    return sorted({w.collection_day for w in windows})


def assert_day_disjoint(train: Sequence[FeatureWindow], test: Sequence[FeatureWindow]) -> None:
    train_days = {w.collection_day for w in train}
    test_days = {w.collection_day for w in test}
    overlap = train_days & test_days
    if overlap:
        raise DayOverlapError(f"train/test partitions share calendar day(s): {sorted(overlap)}")


def day_disjoint_split(
    windows: Sequence[FeatureWindow],
    *,
    test_days: Sequence[str],
) -> DaySplit:
    """Split windows by explicit calendar-day assignment.

    Any day not listed in ``test_days`` goes to train. Raises
    ``ValueError`` if ``test_days`` contains a day absent from ``windows``
    (silently ignoring a typo'd day would produce a misleadingly-labelled
    but effectively-random split).
    """
    available_days = set(distinct_days(windows))
    requested = set(test_days)
    unknown = requested - available_days
    if unknown:
        raise ValueError(f"test_days contains day(s) not present in windows: {sorted(unknown)}")
    if not requested:
        raise ValueError("test_days must be non-empty")
    if requested == available_days:
        raise ValueError("test_days covers every available day; no data would remain for training")

    train = [w for w in windows if w.collection_day not in requested]
    test = [w for w in windows if w.collection_day in requested]
    split = DaySplit(
        train=train,
        test=test,
        train_days=tuple(sorted(available_days - requested)),
        test_days=tuple(sorted(requested)),
    )
    assert_day_disjoint(split.train, split.test)
    return split


def leave_one_day_out_splits(windows: Sequence[FeatureWindow]) -> Iterator[DaySplit]:
    """Yield one ``DaySplit`` per distinct day, holding that day out as test.

    Requires at least 2 distinct days (otherwise there is nothing to train
    on for any fold); raises ``ValueError`` below that.
    """
    days = distinct_days(windows)
    if len(days) < 2:
        raise ValueError(f"leave-one-day-out requires >= 2 distinct days, got {len(days)}: {days}")
    for held_out_day in days:
        yield day_disjoint_split(windows, test_days=[held_out_day])


def enrollment_length_subsets(
    windows: Sequence[FeatureWindow],
    *,
    day_lengths: Sequence[int],
    holdout_days: Sequence[str],
) -> dict[int, list[FeatureWindow]]:
    """PLAN.md Section 10.5 enrollment-length experiment: for each requested
    enrollment length (in distinct days), return the windows from the
    earliest N training days -- holding ``holdout_days`` constant as the
    evaluation partition across every length, as required.

    Days in ``holdout_days`` are always excluded from every returned subset.
    Raises ``ValueError`` if a requested length exceeds the number of
    available (non-holdout) training days, rather than silently truncating.
    """
    holdout = set(holdout_days)
    remaining_days = sorted({w.collection_day for w in windows} - holdout)

    result: dict[int, list[FeatureWindow]] = {}
    for n_days in day_lengths:
        if n_days > len(remaining_days):
            raise ValueError(
                f"requested enrollment length {n_days} days exceeds available training days "
                f"({len(remaining_days)}: {remaining_days})"
            )
        chosen_days = set(remaining_days[:n_days])
        result[n_days] = [w for w in windows if w.collection_day in chosen_days]
    return result
