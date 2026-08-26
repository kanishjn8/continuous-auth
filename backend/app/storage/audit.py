"""Append-only, hash-chained JSONL audit files with bounded retention."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import StorageIntegrityError, StorageUnavailableError
from .permissions import restrict_file

_AUDIT_NAME = re.compile(r"^audit-(\d{4}-\d{2}-\d{2})\.jsonl(?:\.gz)?$")
_GENESIS_HASH = "0" * 64


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class AuditRetentionResult:
    compressed_files: int
    deleted_files: int


class AuditLog:
    """Maintain one independently verifiable audit chain per UTC date."""

    def __init__(self, directory: Path, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self.directory = directory
        self._clock = clock
        self._lock = threading.RLock()
        self._record_ids: set[str] = set()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.verify_all()

    def _files(self) -> list[Path]:
        return sorted(
            path
            for path in self.directory.iterdir()
            if path.is_file() and _AUDIT_NAME.fullmatch(path.name)
        )

    @staticmethod
    def _records(path: Path) -> Iterator[dict[str, Any]]:
        opener: Callable[..., Any] = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        raise StorageIntegrityError(
                            f"blank audit record in {path.name} at line {line_number}"
                        )
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise StorageIntegrityError(
                            f"invalid audit record in {path.name} at line {line_number}"
                        )
                    yield value
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageIntegrityError(f"cannot verify audit file {path.name}: {exc}") from exc

    def _verify_file(self, path: Path) -> tuple[str, set[str]]:
        previous_hash = _GENESIS_HASH
        record_ids: set[str] = set()
        for record in self._records(path):
            claimed_hash = record.get("record_hash")
            without_hash = {key: value for key, value in record.items() if key != "record_hash"}
            actual_hash = hashlib.sha256(_canonical_json(without_hash).encode("utf-8")).hexdigest()
            if record.get("previous_hash") != previous_hash or claimed_hash != actual_hash:
                raise StorageIntegrityError(f"audit hash chain failed in {path.name}")
            record_id = record.get("record_id")
            if not isinstance(record_id, str) or not record_id or record_id in record_ids:
                raise StorageIntegrityError(f"invalid or repeated audit record id in {path.name}")
            record_ids.add(record_id)
            previous_hash = actual_hash
        return previous_hash, record_ids

    def verify_all(self) -> None:
        with self._lock:
            all_record_ids: set[str] = set()
            for path in self._files():
                _, record_ids = self._verify_file(path)
                duplicate = all_record_ids.intersection(record_ids)
                if duplicate:
                    raise StorageIntegrityError("audit record id occurs in more than one file")
                all_record_ids.update(record_ids)
            self._record_ids = all_record_ids

    def append(
        self,
        *,
        event_type: str,
        occurred_at_utc: str,
        correlation_id: str,
        payload: Mapping[str, Any],
        record_id: str | None = None,
    ) -> str:
        """Append and fsync one record; repeated record IDs are idempotent."""

        with self._lock:
            resolved_id = record_id or str(uuid.uuid4())
            if resolved_id in self._record_ids:
                return resolved_id
            now = self._clock().astimezone(UTC)
            path = self.directory / f"audit-{now.date().isoformat()}.jsonl"
            if path.exists():
                previous_hash, _ = self._verify_file(path)
            else:
                previous_hash = _GENESIS_HASH
            record: dict[str, Any] = {
                "audit_schema_version": "1.0.0",
                "record_id": resolved_id,
                "event_type": event_type,
                "occurred_at_utc": occurred_at_utc,
                "logged_at_utc": now.isoformat(timespec="microseconds").replace("+00:00", "Z"),
                "correlation_id": correlation_id,
                "payload": dict(payload),
                "previous_hash": previous_hash,
            }
            record["record_hash"] = hashlib.sha256(
                _canonical_json(record).encode("utf-8")
            ).hexdigest()
            encoded = (_canonical_json(record) + "\n").encode("utf-8")
            try:
                descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
                try:
                    offset = 0
                    while offset < len(encoded):
                        offset += os.write(descriptor, encoded[offset:])
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                restrict_file(path)
            except OSError as exc:
                raise StorageUnavailableError(f"audit append failed: {exc}") from exc
            self._record_ids.add(resolved_id)
            return resolved_id

    def rotate_and_retain(
        self,
        *,
        now: datetime,
        compress_after_days: int,
        delete_after_days: int,
    ) -> AuditRetentionResult:
        """Compress and delete whole daily files according to configured ages."""

        compressed_files = 0
        deleted_files = 0
        current_date = now.astimezone(UTC).date()
        compress_cutoff = current_date - timedelta(days=compress_after_days)
        delete_cutoff = current_date - timedelta(days=delete_after_days)
        with self._lock:
            for path in self._files():
                match = _AUDIT_NAME.fullmatch(path.name)
                if match is None:
                    continue
                file_date = date.fromisoformat(match.group(1))
                if file_date >= current_date:
                    continue
                if file_date <= delete_cutoff:
                    path.unlink()
                    deleted_files += 1
                    continue
                if path.suffix != ".gz" and file_date <= compress_cutoff:
                    self._verify_file(path)
                    compressed_path = path.with_suffix(path.suffix + ".gz")
                    temporary_path = compressed_path.with_suffix(compressed_path.suffix + ".tmp")
                    try:
                        with path.open("rb") as source, gzip.open(temporary_path, "wb") as target:
                            while block := source.read(1024 * 1024):
                                target.write(block)
                        os.replace(temporary_path, compressed_path)
                        restrict_file(compressed_path)
                        path.unlink()
                    except OSError as exc:
                        temporary_path.unlink(missing_ok=True)
                        raise StorageUnavailableError(f"audit rotation failed: {exc}") from exc
                    compressed_files += 1
            self.verify_all()
        return AuditRetentionResult(
            compressed_files=compressed_files,
            deleted_files=deleted_files,
        )
