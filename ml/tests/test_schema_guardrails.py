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
import pytest

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
    "schema_version",
}


def _is_closed_string_type(annotation: object) -> bool:
    """True iff ``annotation`` can never carry an open/free-form string.

    Recurses into every type argument (Union/Optional branches, dict
    key+value types, list/tuple element types) and requires ALL of them to
    be closed -- a container or union is only as safe as its most
    permissive member, so e.g. ``dict[Enum, float]`` is closed but
    ``list[str]`` and ``Optional[str]`` are not: both contain a bare
    ``str`` branch and must be rejected, not blanket-allowed just because
    the outer shape is a dict/list/tuple/Union.
    """
    if annotation is type(None):
        return True
    if annotation in (int, float, bool):
        return True
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return True
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return True
    if origin is not None:
        return all(_is_closed_string_type(a) for a in typing.get_args(annotation))
    return False


def _assert_no_open_string_fields(model: type[pydantic.BaseModel]) -> None:
    for name, field in model.model_fields.items():
        if name in _ALLOWED_IDENTIFIER_FIELDS:
            continue
        ann = field.annotation
        if ann is str:
            raise AssertionError(
                f"{model.__name__}.{name} is an open str field — forbidden by guardrail"
            )
        if not _is_closed_string_type(ann):
            raise AssertionError(
                f"{model.__name__}.{name} (annotation={ann!r}) is not a provably content-free type"
            )


def test_keyboard_event_has_no_open_content_fields():
    _assert_no_open_string_fields(KeyboardEvent)


def test_mouse_event_has_no_open_content_fields():
    _assert_no_open_string_fields(MouseEvent)


def test_context_event_has_no_open_content_fields():
    _assert_no_open_string_fields(ContextEvent)


def test_heartbeat_has_no_open_content_fields():
    _assert_no_open_string_fields(Heartbeat)


def test_context_block_rejects_unknown_category_keys():
    with pytest.raises(pydantic.ValidationError, match="unknown application categories"):
        ContextBlock(
            dominant_category="UNKNOWN",
            category_fractions={"possible-user-content": 1.0},
            app_switch_rate=0.0,
            device_class="UNKNOWN",
        )


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


# --- Guardrail-detector regression tests -----------------------------------
#
# These exercise the detector itself (``_assert_no_open_string_fields``)
# against deliberately-unsafe dummy models -- proving the guardrail would
# actually catch an open-content field if one were ever added to a real
# event/window model, not just that today's real models happen to pass.


class _DummyOptionalStr(pydantic.BaseModel):
    note: str | None = None


class _DummyListStr(pydantic.BaseModel):
    tags: list[str] = pydantic.Field(default_factory=list)


class _DummyDictStrValue(pydantic.BaseModel):
    labels: dict[str, str] = pydantic.Field(default_factory=dict)


def test_guardrail_detector_rejects_optional_str_field():
    with pytest.raises(AssertionError):
        _assert_no_open_string_fields(_DummyOptionalStr)


def test_guardrail_detector_rejects_list_of_str_field():
    with pytest.raises(AssertionError):
        _assert_no_open_string_fields(_DummyListStr)


def test_guardrail_detector_rejects_dict_with_str_value_field():
    with pytest.raises(AssertionError):
        _assert_no_open_string_fields(_DummyDictStrValue)
