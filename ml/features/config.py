"""Loader for ML-pipeline tunables.

See ``ml/config/thresholds.yaml`` for the ASSUMPTION note about why this
file lives under ``ml/config/`` rather than a repo-root ``config/`` (which
does not exist yet), and the ADR-005 `[OPEN]` note on quality-gate values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "thresholds.yaml"


class ConfigError(ValueError):
    """Raised when the loaded configuration is structurally invalid."""


@dataclass(frozen=True)
class WindowingConfig:
    window_seconds: float
    window_keystrokes: int


@dataclass(frozen=True)
class FeatureComputationConfig:
    pause_threshold_us: int
    burst_gap_threshold_us: int
    mouse_segment_gap_us: int
    mouse_micro_pause_us: int
    double_click_max_gap_us: int
    scroll_burst_gap_us: int
    reference_screen_width_px: int
    reference_screen_height_px: int


@dataclass(frozen=True)
class QualityGateConfig:
    min_keystrokes: int
    min_mouse_samples: int


@dataclass(frozen=True)
class MLConfig:
    windowing: WindowingConfig
    feature_computation: FeatureComputationConfig
    quality_gate: QualityGateConfig
    raw: dict[str, Any]


def _require_positive(value: Any, name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{name} must be a positive number, got {value!r}")


def load_config(path: Path | str | None = None) -> MLConfig:
    """Load and validate the ML tunables config.

    Fails loudly (raises ``ConfigError``) on structurally invalid config,
    per the "invalid config fails startup" behavior specified for C9 in
    TASK_DELEGATION.md — this module does not silently fall back to
    hardcoded defaults on a malformed file.
    """
    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise ConfigError(f"config file not found: {cfg_path}")

    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    try:
        w = raw["windowing"]
        fc = raw["feature_computation"]
        qg = raw["quality_gate"]
        pun = raw["per_user_normalization"]
        isof = raw["isolation_forest"]
        maha = raw["mahalanobis_baseline"]
        ocsvm = raw["one_class_svm_baseline"]
    except KeyError as e:
        raise ConfigError(f"missing required config section: {e}") from e

    _require_positive(w["window_seconds"], "windowing.window_seconds")
    _require_positive(w["window_keystrokes"], "windowing.window_keystrokes")
    for key in (
        "pause_threshold_us",
        "burst_gap_threshold_us",
        "mouse_segment_gap_us",
        "mouse_micro_pause_us",
        "double_click_max_gap_us",
        "scroll_burst_gap_us",
        "reference_screen_width_px",
        "reference_screen_height_px",
    ):
        _require_positive(fc[key], f"feature_computation.{key}")
    for key in ("min_keystrokes", "min_mouse_samples"):
        _require_positive(qg[key], f"quality_gate.{key}")

    # Read (deep) via config.raw[...] by ml/training and ml/baselines, but
    # never previously validated here -- a missing/malformed key surfaced as
    # a raw KeyError/TypeError mid-training instead of at config load, which
    # contradicts this function's own "invalid config fails startup"
    # contract. Only the fields that must unconditionally be positive
    # numbers are checked; fields with a valid non-numeric sentinel (e.g.
    # isolation_forest.contamination/max_samples accept "auto",
    # one_class_svm_baseline.kernel/gamma are free-form sklearn strings)
    # are left to sklearn's own validation, since replicating that here
    # would drift out of sync with sklearn's accepted values.
    _require_positive(pun["min_baseline_windows"], "per_user_normalization.min_baseline_windows")
    _require_positive(isof["n_estimators"], "isolation_forest.n_estimators")
    _require_positive(maha["ridge"], "mahalanobis_baseline.ridge")
    _require_positive(ocsvm["nu"], "one_class_svm_baseline.nu")

    return MLConfig(
        windowing=WindowingConfig(
            window_seconds=float(w["window_seconds"]),
            window_keystrokes=int(w["window_keystrokes"]),
        ),
        feature_computation=FeatureComputationConfig(
            pause_threshold_us=int(fc["pause_threshold_us"]),
            burst_gap_threshold_us=int(fc["burst_gap_threshold_us"]),
            mouse_segment_gap_us=int(fc["mouse_segment_gap_us"]),
            mouse_micro_pause_us=int(fc["mouse_micro_pause_us"]),
            double_click_max_gap_us=int(fc["double_click_max_gap_us"]),
            scroll_burst_gap_us=int(fc["scroll_burst_gap_us"]),
            reference_screen_width_px=int(fc["reference_screen_width_px"]),
            reference_screen_height_px=int(fc["reference_screen_height_px"]),
        ),
        quality_gate=QualityGateConfig(
            min_keystrokes=int(qg["min_keystrokes"]),
            min_mouse_samples=int(qg["min_mouse_samples"]),
        ),
        raw=raw,
    )
