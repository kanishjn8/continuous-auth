"""Working fixture schema for IPC events and feature windows.

ASSUMPTION (flag clearly, reconcile when real ``protocol/`` lands)
--------------------------------------------------------------------
``protocol/`` (T-002, owner: Joel) does not exist yet in this repository, and
neither does the native collector (T-004/T-005, owner: Kanish). Per Manas's
instructions and PLAN.md Section 9.5/9.6, this module is a *working fixture*
that reproduces the event and feature-window schemas specified in PLAN.md
Section 7.2 ("IPC Event Schema") and Section 7.3 ("Feature Window Schema")
as closely as possible, using Pydantic v2 (the same library the real
generated bindings will use per PLAN.md Section 15.1). When ``protocol/`` is
generated for real:

  1. Replace the imports in this file with the generated Pydantic models.
  2. Keep ``ml/features/`` consuming the same field names so downstream code
     (windowing, keyboard/mouse feature blocks, quality gating) does not need
     to change.
  3. Delete this fixture module.

Everything below is intentionally scoped to exactly what PLAN.md Section 7.2
specifies, field-for-field, so the diff to reconcile is small.

Privacy note (P4 / ADR-004 / guardrail table, Section 19.3): no field on any
model in this file may carry a character, keycode, word, window title, URL,
or other user-generated text. This is enforced by construction (every string
field is a closed enum) and is asserted by
``ml/tests/test_schema_guardrails.py``.
"""

from __future__ import annotations

import enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class KeyClass(str, enum.Enum):
    """ADR-004 content-free key-class taxonomy (PLAN.md Section 6, ADR-004)."""

    ALPHA_L_HOME = "ALPHA_L_HOME"
    ALPHA_L_UPPER = "ALPHA_L_UPPER"
    ALPHA_L_LOWER = "ALPHA_L_LOWER"
    ALPHA_R_HOME = "ALPHA_R_HOME"
    ALPHA_R_UPPER = "ALPHA_R_UPPER"
    ALPHA_R_LOWER = "ALPHA_R_LOWER"
    DIGIT = "DIGIT"
    PUNCT = "PUNCT"
    SPACE = "SPACE"
    BACKSPACE = "BACKSPACE"
    DELETE = "DELETE"
    ENTER = "ENTER"
    MODIFIER = "MODIFIER"
    NAVIGATION = "NAVIGATION"
    FUNCTION = "FUNCTION"
    OTHER = "OTHER"


# ADR-004 taxonomy carries hand/row structure for a subset of classes only
# (the alphabetic classes). Non-alphabetic classes have no well-defined hand
# or row, and PLAN.md does not specify one — this mapping is an ASSUMPTION
# made for the class-transition-latency features (xhand/samehand/samerow),
# documented here and in ml/features/keyboard.py.
KEY_CLASS_HAND: dict[KeyClass, str] = {
    KeyClass.ALPHA_L_HOME: "L",
    KeyClass.ALPHA_L_UPPER: "L",
    KeyClass.ALPHA_L_LOWER: "L",
    KeyClass.ALPHA_R_HOME: "R",
    KeyClass.ALPHA_R_UPPER: "R",
    KeyClass.ALPHA_R_LOWER: "R",
}

KEY_CLASS_ROW: dict[KeyClass, str] = {
    KeyClass.ALPHA_L_HOME: "HOME",
    KeyClass.ALPHA_L_UPPER: "UPPER",
    KeyClass.ALPHA_L_LOWER: "LOWER",
    KeyClass.ALPHA_R_HOME: "HOME",
    KeyClass.ALPHA_R_UPPER: "UPPER",
    KeyClass.ALPHA_R_LOWER: "LOWER",
}

HOME_ROW_CLASSES = frozenset({KeyClass.ALPHA_L_HOME, KeyClass.ALPHA_R_HOME})
CORRECTION_CLASSES = frozenset({KeyClass.BACKSPACE, KeyClass.DELETE})


class DeviceClass(str, enum.Enum):
    """ADR-009 input device metadata."""

    INTERNAL_KEYBOARD = "INTERNAL_KEYBOARD"
    EXTERNAL_KEYBOARD = "EXTERNAL_KEYBOARD"
    TRACKPAD = "TRACKPAD"
    EXTERNAL_MOUSE = "EXTERNAL_MOUSE"
    UNKNOWN = "UNKNOWN"


class AppCategory(str, enum.Enum):
    """ADR-008 bootstrap application category taxonomy."""

    PRODUCTIVITY = "PRODUCTIVITY"
    BROWSING = "BROWSING"
    DEVELOPMENT = "DEVELOPMENT"
    CREATIVE = "CREATIVE"
    GAMING = "GAMING"
    SYSTEM = "SYSTEM"
    UNKNOWN = "UNKNOWN"


class MouseButton(str, enum.Enum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    MIDDLE = "MIDDLE"


class QualityLabel(str, enum.Enum):
    """ADR-005 window quality/modality-availability label."""

    FULL = "FULL"
    KBD_ONLY = "KBD_ONLY"
    MOUSE_ONLY = "MOUSE_ONLY"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class Provenance(str, enum.Enum):
    """PLAN.md Section 9.6 data provenance rules."""

    SYNTHETIC = "synthetic"
    PUBLIC_DATASET = "public_dataset"
    TEAM_SELF_COLLECTED = "team_self_collected"
    PILOT_COHORT = "pilot_cohort"


class _NoContentModel(BaseModel):
    """Base class forbidding unknown/extra string content fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class KeyboardEvent(_NoContentModel):
    type: Literal["KEY_DOWN", "KEY_UP"]
    t_capture_us: int = Field(ge=0, description="Monotonic capture-time microseconds (ADR-003)")
    key_class: KeyClass
    is_repeat: bool
    device_class: DeviceClass
    app_id: int = Field(ge=0)
    seq: int = Field(ge=0)


class MouseEvent(_NoContentModel):
    type: Literal["MOVE", "BUTTON_DOWN", "BUTTON_UP", "SCROLL"]
    t_capture_us: int = Field(ge=0)
    x: float
    y: float
    button: Optional[MouseButton] = None
    scroll_dx: float = 0.0
    scroll_dy: float = 0.0
    device_class: DeviceClass
    app_id: int = Field(ge=0)
    seq: int = Field(ge=0)


class ContextEvent(_NoContentModel):
    type: Literal["APP_FOCUS_CHANGE"] = "APP_FOCUS_CHANGE"
    t_capture_us: int = Field(ge=0)
    app_id: int = Field(ge=0)
    category: AppCategory
    seq: int = Field(ge=0)


class Heartbeat(_NoContentModel):
    type: Literal["HEARTBEAT"] = "HEARTBEAT"
    t_capture_us: int = Field(ge=0)
    collector_uptime_ms: int = Field(ge=0)
    dropped_events: int = Field(ge=0)
    buffer_high_water: int = Field(ge=0)
    seq: int = Field(ge=0)


class ContextBlock(_NoContentModel):
    """PLAN.md Section 7.3 context block.

    CRITICAL (PLAN.md Section 7.3, "Critical constraint"): this block is a
    supporting signal for the risk engine only. It must never be fed into an
    identity model. See ``FeatureWindow.identity_feature_names`` /
    ``FeatureWindow.to_identity_arrays`` — neither ever reads this block.
    """

    dominant_category: AppCategory
    category_fractions: dict[AppCategory, float]
    app_switch_rate: float = Field(ge=0)
    device_class: DeviceClass

    @field_validator("category_fractions")
    @classmethod
    def _fractions_sum_to_one(cls, v: dict[AppCategory, float]) -> dict[AppCategory, float]:
        total = sum(v.values())
        if v and not (0.99 <= total <= 1.01):
            raise ValueError(f"category_fractions must sum to ~1.0, got {total}")
        return v


class FeatureWindow(_NoContentModel):
    """PLAN.md Section 7.3 feature window.

    ``keyboard_features`` / ``mouse_features`` are ``None`` (not zero- or
    NaN-filled) when that modality's quality gate is not met (ADR-005,
    ADR-006 rationale) — a missing modality must never be represented as an
    anomalous-looking feature vector.
    """

    # Window metadata (Section 7.3 "Window metadata")
    user_id: str
    session_id: str
    segment_id: str
    window_id: str
    t_start_us: int = Field(ge=0)
    t_end_us: int = Field(ge=0)
    quality_label: QualityLabel
    key_event_count: int = Field(ge=0)
    mouse_event_count: int = Field(ge=0)
    collection_day: str = Field(description="Date only (YYYY-MM-DD), for day-disjoint splitting — NOT a feature")
    provenance: Provenance
    feature_schema_version: str = "1.0.0-fixture"

    # Feature blocks
    keyboard_features: Optional[dict[str, float]] = None
    mouse_features: Optional[dict[str, float]] = None
    context: Optional[ContextBlock] = None

    @field_validator("t_end_us")
    @classmethod
    def _end_after_start(cls, v: int, info) -> int:
        start = info.data.get("t_start_us")
        if start is not None and v < start:
            raise ValueError("t_end_us must be >= t_start_us")
        return v

    def to_identity_arrays(self) -> tuple[Optional[dict[str, float]], Optional[dict[str, float]]]:
        """Return (keyboard, mouse) feature dicts only — never context.

        This is the sole path model training/inference code should use to
        pull arrays out of a window, so context can never leak into an
        identity model (PLAN.md Section 7.3 critical constraint, P2).
        """
        return self.keyboard_features, self.mouse_features
