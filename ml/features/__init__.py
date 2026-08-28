"""Shared feature extraction (T-008).

This is the single implementation imported by both training and (eventually)
live inference, per PLAN.md Section 16 structural rule 4 and the T-008
acceptance criteria in TASK_DELEGATION.md.
"""

from ml.features.extractor import extract_windows
from ml.features.schema import (
    FEATURE_SCHEMA_VERSION,
    ContextEvent,
    DeviceClass,
    FeatureWindow,
    Heartbeat,
    KeyboardEvent,
    KeyClass,
    MouseEvent,
    QualityLabel,
)
from ml.features.windowing import WindowBuilder, WindowConfig

__all__ = [
    "ContextEvent",
    "Heartbeat",
    "KeyboardEvent",
    "MouseEvent",
    "FeatureWindow",
    "QualityLabel",
    "DeviceClass",
    "KeyClass",
    "FEATURE_SCHEMA_VERSION",
    "WindowBuilder",
    "WindowConfig",
    "extract_windows",
]
