"""JSON and authoritative C1 contract validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from ml.features.schema import ContextEvent, Heartbeat, KeyboardEvent, MouseEvent
from protocol.generated.python.contracts import AppRegistryEvent, DeviceMetadataEvent

from .types import IngestedEvent

ValidationIssueCode = Literal["MALFORMED_JSON", "INVALID_EVENT_SCHEMA"]


@dataclass(frozen=True)
class ValidationIssue:
    code: ValidationIssueCode
    detail: str


@dataclass(frozen=True)
class ValidationResult:
    event: IngestedEvent | None
    issue: ValidationIssue | None


def validate_payload(payload: bytes) -> ValidationResult:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return ValidationResult(None, ValidationIssue("MALFORMED_JSON", str(exc)))
    if not isinstance(document, dict):
        return ValidationResult(
            None,
            ValidationIssue("INVALID_EVENT_SCHEMA", "event payload must be a JSON object"),
        )
    event_type = document.get("type")
    if not isinstance(event_type, str):
        return ValidationResult(
            None,
            ValidationIssue("INVALID_EVENT_SCHEMA", f"unknown event type {event_type!r}"),
        )
    try:
        event: IngestedEvent
        if event_type in ("KEY_DOWN", "KEY_UP"):
            event = KeyboardEvent.model_validate(document)
        elif event_type in ("MOVE", "BUTTON_DOWN", "BUTTON_UP", "SCROLL"):
            event = MouseEvent.model_validate(document)
        elif event_type == "APP_FOCUS_CHANGE":
            event = ContextEvent.model_validate(document)
        elif event_type == "HEARTBEAT":
            event = Heartbeat.model_validate(document)
        elif event_type == "APP_REGISTRY":
            event = AppRegistryEvent.model_validate(document)
        elif event_type == "DEVICE_METADATA":
            event = DeviceMetadataEvent.model_validate(document)
        else:
            return ValidationResult(
                None,
                ValidationIssue("INVALID_EVENT_SCHEMA", f"unknown event type {event_type!r}"),
            )
    except ValidationError as exc:
        return ValidationResult(None, ValidationIssue("INVALID_EVENT_SCHEMA", str(exc)))
    return ValidationResult(event, None)
