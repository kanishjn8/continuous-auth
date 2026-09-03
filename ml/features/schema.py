"""ML-facing adapters for the authoritative generated protocol contracts.

``protocol/generated/python/contracts.py`` is the only source of shared
event and feature-window shapes. This module supplies small conveniences
needed by the feature implementation (constructor defaults, enum aliases,
and content-free key-class groupings) without defining a parallel schema.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Literal

from pydantic import field_validator

from protocol.generated.python.contracts import (
    PROTOCOL_VERSION,
    ApplicationCategory,
    DataProvenance,
    InputDeviceClass,
    KeyClass,
    MouseButton,
    WindowQuality,
)
from protocol.generated.python.contracts import (
    AppFocusShare as GeneratedAppFocusShare,
)
from protocol.generated.python.contracts import (
    ContextBlock as GeneratedContextBlock,
)
from protocol.generated.python.contracts import (
    ContextEvent as GeneratedContextEvent,
)
from protocol.generated.python.contracts import (
    FeatureWindow as GeneratedFeatureWindow,
)
from protocol.generated.python.contracts import (
    Heartbeat as GeneratedHeartbeat,
)
from protocol.generated.python.contracts import (
    KeyboardEvent as GeneratedKeyboardEvent,
)
from protocol.generated.python.contracts import (
    KeyboardFeatures as GeneratedKeyboardFeatures,
)
from protocol.generated.python.contracts import (
    MouseEvent as GeneratedMouseEvent,
)
from protocol.generated.python.contracts import (
    MouseFeatures as GeneratedMouseFeatures,
)

AppCategory = ApplicationCategory
DeviceClass = InputDeviceClass
Provenance = DataProvenance
QualityLabel = WindowQuality
FEATURE_SCHEMA_VERSION = PROTOCOL_VERSION

__all__ = [
    "AppCategory",
    "AppFocusShare",
    "ContextBlock",
    "ContextEvent",
    "CORRECTION_CLASSES",
    "DeviceClass",
    "FEATURE_SCHEMA_VERSION",
    "FeatureWindow",
    "Heartbeat",
    "HOME_ROW_CLASSES",
    "KeyboardEvent",
    "KeyboardFeatures",
    "KEY_CLASS_HAND",
    "KEY_CLASS_ROW",
    "KeyClass",
    "MouseButton",
    "MouseEvent",
    "MouseFeatures",
    "Provenance",
    "QualityLabel",
]


# ADR-004 defines hand/row structure only for alphabetic key classes. These
# content-free lookup tables support aggregate transition-latency features;
# no raw platform key identifier reaches this module.
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


class KeyboardEvent(GeneratedKeyboardEvent):
    """Canonical keyboard event with the current version supplied locally."""

    schema_version: Literal["1.0.0"] = PROTOCOL_VERSION


class MouseEvent(GeneratedMouseEvent):
    """Canonical mouse event with explicit neutral defaults for unused fields."""

    schema_version: Literal["1.0.0"] = PROTOCOL_VERSION
    button: MouseButton | None = None
    scroll_dx: int = 0
    scroll_dy: int = 0


class ContextEvent(GeneratedContextEvent):
    schema_version: Literal["1.0.0"] = PROTOCOL_VERSION
    type: Literal["APP_FOCUS_CHANGE"] = "APP_FOCUS_CHANGE"


class Heartbeat(GeneratedHeartbeat):
    schema_version: Literal["1.0.0"] = PROTOCOL_VERSION
    type: Literal["HEARTBEAT"] = "HEARTBEAT"


class _FeatureMapping:
    """Read-only mapping conveniences for generated aggregate feature blocks."""

    def __getitem__(self, name: str) -> float:
        value = getattr(self, name)
        if not isinstance(value, (int, float)):
            raise KeyError(name)
        return float(value)

    def keys(self) -> Iterator[str]:
        return iter(self.__dict__)


class KeyboardFeatures(_FeatureMapping, GeneratedKeyboardFeatures):
    pass


class MouseFeatures(_FeatureMapping, GeneratedMouseFeatures):
    pass


AppFocusShare = GeneratedAppFocusShare


class ContextBlock(GeneratedContextBlock):
    """Canonical context with a stricter application-category key boundary."""

    app_shares: list[AppFocusShare]

    @field_validator("category_fractions")
    @classmethod
    def validate_category_fractions(cls, value: dict[str, float]) -> dict[str, float]:
        allowed = {category.value for category in AppCategory}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown application categories: {sorted(unknown)!r}")
        total = sum(value.values())
        if value and not 0.99 <= total <= 1.01:
            raise ValueError(f"category_fractions must sum to approximately 1.0, got {total}")
        return value

    @field_validator("app_shares")
    @classmethod
    def validate_app_shares(cls, value: list[AppFocusShare]) -> list[AppFocusShare]:
        """Application shares partition the same window as category fractions.

        A duplicated ``(app_id, category)`` pair would double-count that
        application's weight in the confidence mixture, so it is rejected here
        rather than silently skewing the per-application empirical statistics.
        """

        keys = [(share.app_id, share.category) for share in value]
        if len(keys) != len(set(keys)):
            raise ValueError("app_shares must not repeat an (app_id, category) pair")
        total = sum(share.fraction for share in value)
        if value and not 0.99 <= total <= 1.01:
            raise ValueError(f"app_shares fractions must sum to approximately 1.0, got {total}")
        return value


class FeatureWindow(GeneratedFeatureWindow):
    """Canonical feature window with ML-only read helpers."""

    schema_version: Literal["1.0.0"] = PROTOCOL_VERSION
    keyboard_features: KeyboardFeatures | None
    mouse_features: MouseFeatures | None
    context: ContextBlock

    def to_identity_arrays(
        self,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Return only identity feature blocks; context is never included."""

        keyboard = (
            self.keyboard_features.model_dump(mode="python")
            if self.keyboard_features is not None
            else None
        )
        mouse = (
            self.mouse_features.model_dump(mode="python")
            if self.mouse_features is not None
            else None
        )
        return keyboard, mouse
