from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from protocol.generated.python.contracts import (
    PROTOCOL_VERSION,
    AppRegistryEntry,
    FeatureWindow,
    KeyboardEvent,
    ModalityScore,
    RiskDecision,
    WebSocketEnvelope,
)

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = json.loads(
    (ROOT / "protocol" / "schemas" / "contracts.schema.json").read_text(encoding="utf-8")
)


def validator(definition: str) -> Draft202012Validator:
    schema = {
        "$schema": CATALOGUE["$schema"],
        "$defs": CATALOGUE["$defs"],
        "$ref": f"#/$defs/{definition}",
    }
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def load_example(kind: str, name: str) -> dict[str, object]:
    path = ROOT / "protocol" / "examples" / kind / name
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["keyboard_event.json", "heartbeat.json"])
def test_valid_event_examples(name: str) -> None:
    validator("EventFrame").validate(load_example("valid", name))


@pytest.mark.parametrize("name", ["event_with_raw_identifier.json", "wrong_protocol_version.json"])
def test_invalid_event_examples_are_rejected(name: str) -> None:
    assert list(validator("EventFrame").iter_errors(load_example("invalid", name)))


def test_insufficient_window_cannot_carry_identity_features() -> None:
    validator("FeatureWindow").validate(load_example("valid", "insufficient_feature_window.json"))
    errors = list(
        validator("FeatureWindow").iter_errors(
            load_example("invalid", "insufficient_window_with_features.json")
        )
    )
    assert errors


def test_insufficient_decision_is_a_non_enforcing_hold() -> None:
    decision = load_example("valid", "insufficient_risk_decision.json")
    validator("RiskDecision").validate(decision)
    for invalid_change in (
        {"action": "REAUTH"},
        {"enforcement_applied": True},
        {"fused_score": 0.9},
    ):
        invalid = {**decision, **invalid_change}
        assert list(validator("RiskDecision").iter_errors(invalid))


def test_generated_pydantic_binding_is_strict() -> None:
    event = load_example("valid", "keyboard_event.json")
    assert KeyboardEvent.model_validate(event).seq == 884213
    with pytest.raises(ValidationError):
        KeyboardEvent.model_validate({**event, "keycode": 65})


def test_generated_pydantic_binding_enforces_cross_field_contracts() -> None:
    with pytest.raises(ValidationError):
        FeatureWindow.model_validate(
            load_example("invalid", "insufficient_window_with_features.json")
        )
    with pytest.raises(ValidationError):
        ModalityScore.model_validate(
            {
                "available": False,
                "raw_score": 0.4,
                "calibrated_score": 0.5,
                "status": "UNAVAILABLE",
            }
        )
    decision = load_example("valid", "insufficient_risk_decision.json")
    with pytest.raises(ValidationError):
        RiskDecision.model_validate({**decision, "enforcement_applied": True})


def test_websocket_event_type_must_match_its_payload() -> None:
    alert = {
        "alert_id": "synthetic-alert-1",
        "alert_type": "AVAILABILITY",
        "severity": "HIGH",
        "code": "COLLECTOR_HEARTBEAT_LOST",
        "occurred_at": "2026-08-25T12:00:00Z",
        "acknowledged": False,
    }
    envelope = {
        "schema_version": "1.0.0",
        "stream_seq": 9,
        "event_type": "ALERT",
        "emitted_at": "2026-08-25T12:00:01Z",
        "payload": alert,
    }
    validator("WebSocketEnvelope").validate(envelope)
    assert WebSocketEnvelope.model_validate(envelope).stream_seq == 9
    invalid = {**envelope, "event_type": "STATE"}
    assert list(validator("WebSocketEnvelope").iter_errors(invalid))
    with pytest.raises(ValidationError):
        WebSocketEnvelope.model_validate(invalid)


def test_configuration_schema_enforces_security_boundaries_without_defaults() -> None:
    unresolved_values_fixture = {
        "config_version": "experiment-required",
        "protocol_version": "1.0.0",
        "collector": {
            "heartbeat_interval_seconds": 1,
            "buffer_capacity": 1,
            "reconnect_initial_seconds": 1,
            "reconnect_max_seconds": 1,
            "reconnect_multiplier": 2,
            "poll_interval_ms": 1,
            "context_refresh_ms": 1,
            "device_refresh_seconds": 1,
            "pipe_name": "continuous-auth-test",
            "pause_event_name": "continuous-auth-test-pause",
            "overload_policy": "DROP_OLDEST",
        },
        "ingestion": {
            "max_frame_bytes": 1,
            "raw_event_ring_capacity": 1,
            "idle_split_seconds": 1,
        },
        "windowing": {
            "duration_seconds": 1,
            "keystroke_limit": 1,
            "min_keystrokes": 1,
            "min_mouse_samples": 1,
        },
        "enrollment": {
            "min_windows": 1,
            "min_distinct_days": 1,
            "calibration_windows": 1,
        },
        "context": {
            "confidence_floor": 0.1,
            "min_empirical_observations": 1,
            "bootstrap_weights": {"UNKNOWN": 1.0},
            "adjustment_mode": "PER_CONTEXT_NORMALIZATION",
            "variance_scale": 4.0,
            "normalization_min_scale": 0.02,
        },
        "risk": {
            "keyboard_weight": 1,
            "mouse_weight": 1,
            "ewma_alpha": 0.5,
            "medium_threshold": 0.5,
            "high_threshold": 0.8,
            "breach_k": 2,
            "breach_n": 2,
            "cooldown_seconds": 1,
            "action_budget": 1,
        },
        "storage": {
            "environment": "PILOT",
            "data_policy": "APPROVED_COLLECTION",
            "root_directory": "C:/approved-local-store",
            "database_filename": "continuous-auth.db",
            "audit_directory_name": "audit",
            "busy_timeout_ms": 1,
        },
        "retention": {
            "feature_window_days": 1,
            "score_days": 1,
            "audit_compress_after_days": 1,
            "audit_delete_after_days": 1,
            "raw_debug_capture_enabled": False,
            "pilot_mode": True,
        },
        "update_manager": {
            "min_promotable_windows": 1,
            "quarantine_days": 1,
            "retraining_cadence_days": 1,
            "regression_tolerance": 0,
            "scheduled_anchor_interval_seconds": 1,
            "retained_model_versions": 2,
        },
        "api": {
            "bind_host": "127.0.0.1",
            "port": 8765,
            "session_ttl_seconds": 1,
            "secret_hash_iterations": 100000,
            "max_sessions": 1,
            "default_page_size": 1,
            "max_page_size": 2,
            "snapshot_alert_limit": 1,
            "websocket_client_capacity": 1,
            "replay_event_capacity": 1,
        },
    }
    validator("RuntimeConfig").validate(unresolved_values_fixture)
    invalid_cases = (
        {
            **unresolved_values_fixture,
            "context": {**unresolved_values_fixture["context"], "confidence_floor": 0},
        },
        {
            **unresolved_values_fixture,
            "retention": {
                **unresolved_values_fixture["retention"],
                "raw_debug_capture_enabled": True,
            },
        },
        {
            **unresolved_values_fixture,
            "api": {**unresolved_values_fixture["api"], "bind_host": "0.0.0.0"},
        },
        {
            **unresolved_values_fixture,
            "storage": {
                **unresolved_values_fixture["storage"],
                "environment": "DEVELOPMENT",
                "data_policy": "APPROVED_COLLECTION",
            },
        },
    )
    for invalid in invalid_cases:
        assert list(validator("RuntimeConfig").iter_errors(invalid))


def test_app_registry_contract_accepts_process_name_only() -> None:
    entry = {
        "schema_version": "1.0.0",
        "app_id": 17,
        "process_name": "synthetic.exe",
        "category": "UNKNOWN",
    }
    validator("AppRegistryEntry").validate(entry)
    assert AppRegistryEntry.model_validate(entry).app_id == 17
    invalid = {**entry, "process_name": "C:/private/document.exe"}
    assert list(validator("AppRegistryEntry").iter_errors(invalid))
    with pytest.raises(ValidationError):
        AppRegistryEntry.model_validate(invalid)


def test_codegen_is_deterministic_and_committed_output_is_current() -> None:
    result = subprocess.run(
        [sys.executable, "protocol/codegen/generate.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_all_catalogue_definitions_are_valid_json_schema() -> None:
    Draft202012Validator.check_schema(CATALOGUE)


def test_protocol_version_file_matches_generated_binding() -> None:
    assert (ROOT / "protocol" / "VERSION").read_text(encoding="utf-8").strip() == PROTOCOL_VERSION


def test_openapi_declares_required_authenticated_surfaces_and_safe_errors() -> None:
    text = (ROOT / "protocol" / "schemas" / "api.openapi.yaml").read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    assert document["openapi"] == "3.1.0"
    assert document["security"] == [{"localSession": []}]
    assert len(document["paths"]) == 18
    required_tokens = (
        "openapi: 3.1.0",
        "security:",
        "/v1/state:",
        "/v1/profiles:",
        "/v1/history/decisions:",
        "/v1/alerts:",
        "/v1/metrics:",
        "/v1/health:",
        "/v1/auth/login:",
        "/v1/updates:",
        "/v1/admin/models/{user_id}/rollback:",
        "/v1/enforcement/challenge:",
        "/v1/enforcement/challenge/{decision_id}/respond:",
        "/v1/enforcement/status:",
        "/v1/collection/provenance:",
        "correlation_id:",
    )
    assert all(token in text for token in required_tokens)
    assert "feature_vectors" not in text


def _property_names(node: object) -> set[str]:
    """Every property name declared anywhere beneath a schema node."""

    found: set[str] = set()
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            found.update(str(key) for key in properties)
        for value in node.values():
            found |= _property_names(value)
    elif isinstance(node, list):
        for item in node:
            found |= _property_names(item)
    return found


def test_openapi_never_returns_a_security_challenge_answer() -> None:
    """No enforcement operation may declare an answer field in a response."""

    document = yaml.safe_load(
        (ROOT / "protocol" / "schemas" / "api.openapi.yaml").read_text(encoding="utf-8")
    )
    checked = 0
    for path, operations in document["paths"].items():
        if not path.startswith("/v1/enforcement"):
            continue
        for method, operation in operations.items():
            if method == "parameters" or not isinstance(operation, dict):
                continue
            checked += 1
            exposed = {
                name
                for name in _property_names(operation.get("responses", {}))
                if "answer" in name
            }
            assert not exposed, f"{method} {path} response exposes {sorted(exposed)}"
    assert checked == 4
