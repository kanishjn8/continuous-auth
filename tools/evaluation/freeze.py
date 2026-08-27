"""Lock exact configuration and corpus checksums before final evaluation."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


class EvaluationFreezeError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise EvaluationFreezeError(f"evaluation input could not be read: {exc}") from exc


def _canonical_yaml_checksum(path: Path) -> str:
    try:
        document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EvaluationFreezeError(f"configuration could not be parsed: {exc}") from exc
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def create_evaluation_freeze(
    destination: Path,
    *,
    config_paths: list[Path],
    dataset_manifest: Path,
    code_revision: str,
    frozen_at: datetime | None = None,
) -> dict[str, Any]:
    if destination.exists():
        raise EvaluationFreezeError("evaluation freeze already exists and cannot be overwritten")
    if not code_revision.strip():
        raise EvaluationFreezeError("code revision is required")
    config_records = [
        {
            "name": path.name,
            "content_checksum": _sha256(path),
            "canonical_checksum": _canonical_yaml_checksum(path),
        }
        for path in sorted(config_paths, key=lambda value: value.name)
    ]
    if len({record["name"] for record in config_records}) != len(config_records):
        raise EvaluationFreezeError("configuration file names must be unique")
    base = {
        "freeze_schema": "continuous-auth-evaluation-freeze-v1",
        "frozen_at": (frozen_at or datetime.now(UTC))
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "code_revision": code_revision,
        "dataset_manifest_name": dataset_manifest.name,
        "dataset_manifest_checksum": _sha256(dataset_manifest),
        "configs": config_records,
    }
    canonical = json.dumps(base, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    document = {**base, "freeze_checksum": hashlib.sha256(canonical.encode()).hexdigest()}
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except OSError as exc:
        raise EvaluationFreezeError(f"evaluation freeze could not be written: {exc}") from exc
    return document


def verify_evaluation_freeze(
    freeze_path: Path,
    *,
    config_directory: Path,
    dataset_manifest: Path,
    code_revision: str,
) -> None:
    try:
        document: Any = json.loads(freeze_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationFreezeError(f"evaluation freeze could not be read: {exc}") from exc
    checksum = document.pop("freeze_checksum", None)
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if checksum != hashlib.sha256(canonical.encode()).hexdigest():
        raise EvaluationFreezeError("evaluation freeze checksum does not match")
    if document["code_revision"] != code_revision:
        raise EvaluationFreezeError("code revision changed after evaluation freeze")
    if document["dataset_manifest_checksum"] != _sha256(dataset_manifest):
        raise EvaluationFreezeError("dataset manifest changed after evaluation freeze")
    for record in document["configs"]:
        path = config_directory / record["name"]
        if record["content_checksum"] != _sha256(path):
            raise EvaluationFreezeError(f"configuration changed after freeze: {record['name']}")
        if record["canonical_checksum"] != _canonical_yaml_checksum(path):
            raise EvaluationFreezeError(f"configuration semantics changed: {record['name']}")
