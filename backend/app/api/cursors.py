"""Opaque authenticated pagination cursors bound to one query shape."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json


class InvalidCursor(ValueError):
    pass


class CursorCodec:
    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("cursor signing secret is too short")
        self._secret = secret

    def encode(self, *, resource: str, offset: int, scope: str = "") -> str:
        payload = json.dumps(
            {"resource": resource, "offset": offset, "scope": scope},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(self._secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload + signature).decode("ascii").rstrip("=")

    def decode(self, value: str | None, *, resource: str, scope: str = "") -> int:
        if value is None:
            return 0
        try:
            padded = value + "=" * (-len(value) % 4)
            document = base64.urlsafe_b64decode(padded.encode("ascii"))
            payload, signature = document[:-32], document[-32:]
            expected = hmac.new(self._secret, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise InvalidCursor("cursor signature is invalid")
            parsed = json.loads(payload)
            if parsed != {
                "resource": resource,
                "offset": parsed.get("offset"),
                "scope": scope,
            }:
                raise InvalidCursor("cursor belongs to another query")
            offset = parsed["offset"]
            if not isinstance(offset, int) or offset < 0:
                raise InvalidCursor("cursor offset is invalid")
            return offset
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            if isinstance(exc, InvalidCursor):
                raise
            raise InvalidCursor("cursor is malformed") from exc
