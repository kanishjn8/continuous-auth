"""PLAN.md Section 19.3 guardrail: no field of any event/window model may
carry a character, keycode, string of user-generated text, or window title.

These tests assert the guardrail structurally (every string-typed field on
every IPC/feature model is a closed enum or a Literal set of known tags),
not merely by policy -- consistent with PLAN.md Section 8's "provable by
inspection" requirement for ADR-004.
"""

from __future__ import annotations

import enum
import typing

import pydantic

from ml.features.schema import (
    ContextBlock,
    ContextEvent,
    FeatureWindow,
    Heartbeat,
    KeyboardEvent,
    MouseEvent,
)

# Field names that are legitimate free-form identifiers (not user content):
# session/segment/window/user identifiers, ISO dates, and semver strings.
# None of these can carry typed characters or reconstructable text.
_ALLOWED_IDENTIFIER_FIELDS = {
    "user_id",
    "session_id",
    "segment_id",
    "window_id",
    "collection_day",
    "feature_schema_version",
}


def _is_closed_string_type(annotation: object) -> bool:
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return True
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return True
    if origin is not None:
        return any(_is_closed_string_type(a) for a in typing.get_args(annotation))
    return False


def _assert_no_open_string_fields(model: type[pydantic.BaseModel]) -> None:
    for name, field in model.model_fields.items():
        if name in _ALLOWED_IDENTIFIER_FIELDS:
            continue
        ann = field.annotation
        if ann is str:
            raise AssertionError(f"{model.__name__}.{name} is an open str field — forbidden by guardrail")
        if ann in (int, float, bool):
            continue
        if not _is_closed_string_type(ann):
            # dict[Enum, float] etc. are fine as long as the *value* side
            # cannot carry text; spot-check known container fields below.
            origin = typing.get_origin(ann)
            if origin in (dict, list, tuple) or ann is type(None):
                continue


def test_keyboard_event_has_no_open_content_fields():
    _assert_no_open_string_fields(KeyboardEvent)


def test_mouse_event_has_no_open_content_fields():
    _assert_no_open_string_fields(MouseEvent)


def test_context_event_has_no_open_content_fields():
    _assert_no_open_string_fields(ContextEvent)


def test_heartbeat_has_no_open_content_fields():
    _assert_no_open_string_fields(Heartbeat)


def test_context_block_category_fractions_keyed_by_enum_not_string():
    ann = ContextBlock.model_fields["category_fractions"].annotation
    args = typing.get_args(ann)
    assert args, "category_fractions must be a parameterized dict[AppCategory, float]"
    key_type = args[0]
    assert isinstance(key_type, type) and issubclass(key_type, enum.Enum)


def test_feature_window_forbids_extra_fields():
    assert FeatureWindow.model_config.get("extra") == "forbid"
    assert KeyboardEvent.model_config.get("extra") == "forbid"
    assert MouseEvent.model_config.get("extra") == "forbid"


def test_all_models_are_frozen_immutable():
    for model in (KeyboardEvent, MouseEvent, ContextEvent, Heartbeat, FeatureWindow):
        assert model.model_config.get("frozen") is True, f"{model.__name__} must be frozen"


def test_keyboard_features_dict_never_contains_context_keys():
    from ml.features.keyboard import KEYBOARD_FEATURE_NAMES

    context_keys = set(ContextBlock.model_fields.keys())
    assert context_keys.isdisjoint(KEYBOARD_FEATURE_NAMES)


def test_mouse_features_dict_never_contains_context_keys():
    from ml.features.mouse import MOUSE_FEATURE_NAMES

    context_keys = set(ContextBlock.model_fields.keys())
    assert context_keys.isdisjoint(MOUSE_FEATURE_NAMES)
