"""Versioned execution ledger for every robustness scenario in PLAN §13.6."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class FaultCase(StrEnum):
    NORMAL_GENUINE = "NORMAL_GENUINE"
    BRIEF_UNUSUAL = "BRIEF_UNUSUAL"
    SUSTAINED_UNUSUAL = "SUSTAINED_UNUSUAL"
    IDLE_AWAY = "IDLE_AWAY"
    RAPID_APP_SWITCHING = "RAPID_APP_SWITCHING"
    HIGH_VARIANCE_APP = "HIGH_VARIANCE_APP"
    IMPOSTOR_TAKEOVER = "IMPOSTOR_TAKEOVER"
    MULTI_USER_SELECTION = "MULTI_USER_SELECTION"
    COLLECTOR_KILLED = "COLLECTOR_KILLED"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    MODEL_INVALID = "MODEL_INVALID"
    MALFORMED_OR_OUT_OF_ORDER = "MALFORMED_OR_OUT_OF_ORDER"
    DASHBOARD_DISCONNECTED = "DASHBOARD_DISCONNECTED"
    DEVICE_CHANGED = "DEVICE_CHANGED"


EXPECTED_BEHAVIOR: dict[FaultCase, str] = {
    FaultCase.NORMAL_GENUINE: "sustained LOW and no enforcement",
    FaultCase.BRIEF_UNUSUAL: "no escalation beyond MEDIUM and no enforcement",
    FaultCase.SUSTAINED_UNUSUAL: "graded escalation and recovery",
    FaultCase.IDLE_AWAY: "INSUFFICIENT_DATA hold and no escalation",
    FaultCase.RAPID_APP_SWITCHING: "confidence adjustment with monitoring active",
    FaultCase.HIGH_VARIANCE_APP: "confidence floor enforced with monitoring active",
    FaultCase.IMPOSTOR_TAKEOVER: "escalation latency measured",
    FaultCase.MULTI_USER_SELECTION: "correct independent profile selected",
    FaultCase.COLLECTOR_KILLED: "tamper alert and DEGRADED fail-open state",
    FaultCase.BACKEND_UNAVAILABLE: "fail-open and HIGH availability alert",
    FaultCase.MODEL_INVALID: "model refused, DEGRADED, and alert",
    FaultCase.MALFORMED_OR_OUT_OF_ORDER: "event rejected and counted; pipeline continues",
    FaultCase.DASHBOARD_DISCONNECTED: "enforcement unaffected and snapshot resynchronizes",
    FaultCase.DEVICE_CHANGED: "change logged and surfaced for analysis",
}


@dataclass(frozen=True)
class FaultResult:
    case: FaultCase
    status: str
    evidence_reference: str | None
    observed_behavior: str
    limitation: str | None = None


def load_fault_matrix(path: Path) -> tuple[FaultCase, ...]:
    try:
        document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        values = tuple(FaultCase(value) for value in document["fault_matrix"])
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"fault matrix rejected: {exc}") from exc
    missing = set(FaultCase) - set(values)
    duplicates = len(values) != len(set(values))
    if missing or duplicates:
        raise ValueError("fault matrix must contain every scenario exactly once")
    return values


def write_report(
    destination: Path,
    *,
    config_version: str,
    results: list[FaultResult],
    code_revision: str,
    generated_at: datetime | None = None,
) -> str:
    if set(result.case for result in results) != set(FaultCase):
        raise ValueError("fault report requires one result for every scenario")
    document = {
        "report_schema": "continuous-auth-fault-matrix-v1",
        "config_version": config_version,
        "code_revision": code_revision,
        "generated_at": (generated_at or datetime.now(UTC)).isoformat().replace("+00:00", "Z"),
        "results": [
            {
                **asdict(result),
                "case": result.case.value,
                "expected": EXPECTED_BEHAVIOR[result.case],
            }
            for result in results
        ],
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    checksum = hashlib.sha256(canonical.encode()).hexdigest()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps({**document, "report_checksum": checksum}, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return checksum
