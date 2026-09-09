from __future__ import annotations

import pytest
import yaml

from pathlib import Path

from ml.features.config import ConfigError, load_config
from tools.collection.config import load_collection_settings
from tools.collection.freeze import _partition_days

_REPO_ROOT = Path(__file__).resolve().parents[2]

_BASE = {
    "windowing": {"window_seconds": 30.0, "window_keystrokes": 100},
    "feature_computation": {
        "pause_threshold_us": 2_000_000,
        "burst_gap_threshold_us": 400_000,
        "mouse_segment_gap_us": 500_000,
        "mouse_micro_pause_us": 150_000,
        "double_click_max_gap_us": 500_000,
        "scroll_burst_gap_us": 300_000,
        "reference_screen_width_px": 1920,
        "reference_screen_height_px": 1080,
    },
    "quality_gate": {"min_keystrokes": 5, "min_mouse_samples": 10},
    "per_user_normalization": {"min_baseline_windows": 20},
    "isolation_forest": {
        "n_estimators": 100,
        "contamination": "auto",
        "max_samples": "auto",
        "random_state": 42,
    },
    "mahalanobis_baseline": {"ridge": 0.1},
    "one_class_svm_baseline": {"kernel": "rbf", "nu": 0.1, "gamma": "scale"},
    "enrollment": {"min_train_windows": 20, "min_train_distinct_days": 2},
}


def _write(tmp_path, overrides=None, remove=None):
    cfg = {section: dict(values) for section, values in _BASE.items()}
    for section, values in (overrides or {}).items():
        cfg.setdefault(section, {}).update(values)
    for section in remove or []:
        cfg.pop(section, None)
    path = tmp_path / "thresholds.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def test_load_config_accepts_a_fully_valid_file(tmp_path):
    cfg = load_config(_write(tmp_path))
    assert cfg.windowing.window_seconds == 30.0


def test_load_config_still_accepts_auto_sentinel_fields(tmp_path):
    # isolation_forest.contamination/max_samples support sklearn's "auto"
    # string sentinel and must not be forced into the positive-number check.
    cfg = load_config(_write(tmp_path))
    assert cfg.raw["isolation_forest"]["contamination"] == "auto"
    assert cfg.raw["isolation_forest"]["max_samples"] == "auto"


@pytest.mark.parametrize(
    "remove_section",
    [
        "per_user_normalization",
        "isolation_forest",
        "mahalanobis_baseline",
        "one_class_svm_baseline",
        "enrollment",
    ],
)
def test_load_config_rejects_missing_baseline_sections(tmp_path, remove_section):
    # These sections are read via config.raw[...] deep inside
    # ml/training and ml/baselines but were previously never validated at
    # load time, contradicting this module's own "invalid config fails
    # startup" contract -- a missing section surfaced as a raw KeyError
    # mid-training instead.
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, remove=[remove_section]))


def test_load_config_rejects_non_positive_min_baseline_windows(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            _write(tmp_path, overrides={"per_user_normalization": {"min_baseline_windows": 0}})
        )


def test_load_config_rejects_non_positive_mahalanobis_ridge(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, overrides={"mahalanobis_baseline": {"ridge": -1.0}}))


def test_load_config_rejects_non_positive_isolation_forest_n_estimators(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, overrides={"isolation_forest": {"n_estimators": 0}}))


def test_load_config_rejects_non_positive_one_class_svm_nu(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, overrides={"one_class_svm_baseline": {"nu": 0}}))


def test_load_config_rejects_non_positive_min_train_windows(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, overrides={"enrollment": {"min_train_windows": 0}}))


def test_load_config_rejects_non_positive_min_train_distinct_days(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, overrides={"enrollment": {"min_train_distinct_days": 0}}))


def test_ml_config_exposes_the_training_admission_policy():
    """The shipped config must carry the ADR-014 TRAIN-scoped policy.

    This is a different policy from config/risk.*.yaml's enrollment
    thresholds, which govern the live state machine over the user's whole
    history. Conflating them turned a "3 distinct days" rule into an
    undocumented "6 collection days" requirement.
    """
    config = load_config(_REPO_ROOT / "config" / "ml.development.yaml")
    enrollment = config.raw["enrollment"]
    assert enrollment["min_train_windows"] >= 1
    assert enrollment["min_train_distinct_days"] >= 2


def test_partitioning_can_satisfy_the_train_day_policy():
    """The freeze split and the training-admission policy must agree.

    Nothing checked this before, which is how config/collection.pilot.yaml
    and the enrollment day threshold came to disagree silently: a 60/20/20
    day-disjoint split gives TRAIN roughly half the collected days, so a
    "minimum collection days" number and a "minimum TRAIN days" number are
    never the same number.
    """
    settings = load_collection_settings(_REPO_ROOT / "config" / "collection.pilot.yaml")
    policy = load_config(_REPO_ROOT / "config" / "ml.development.yaml").raw["enrollment"]

    # The splittability floor alone is NOT enough to train a first profile.
    floor_days = [f"2026-09-{index:02d}" for index in range(5, 5 + settings.min_distinct_days)]
    floor_train = {
        day for day, part in _partition_days(floor_days, settings).items() if part == "TRAIN"
    }
    assert len(floor_train) < policy["min_train_distinct_days"]

    # One more collection day is what actually makes a first profile possible.
    enough_days = floor_days + [f"2026-09-{5 + settings.min_distinct_days:02d}"]
    enough_train = {
        day for day, part in _partition_days(enough_days, settings).items() if part == "TRAIN"
    }
    assert len(enough_train) >= policy["min_train_distinct_days"]
