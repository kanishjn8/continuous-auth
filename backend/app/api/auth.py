"""Short-lived local dashboard sessions with no plaintext secret persistence."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class SessionIdentity:
    expires_at: datetime


class LocalSessionAuth:
    def __init__(
        self,
        expected_secret: str,
        *,
        ttl: timedelta,
        hash_iterations: int,
        max_sessions: int,
    ) -> None:
        if not expected_secret:
            raise ValueError("local dashboard secret must not be empty")
        if ttl <= timedelta(0):
            raise ValueError("local session lifetime must be positive")
        if hash_iterations < 100_000 or max_sessions < 1:
            raise ValueError("local authentication security settings are invalid")
        self._salt = secrets.token_bytes(32)
        self._hash_iterations = hash_iterations
        self._expected = self._digest_secret(expected_secret)
        self._ttl = ttl
        self._max_sessions = max_sessions
        self._sessions: dict[bytes, datetime] = {}

    def _digest_secret(self, value: str) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256", value.encode("utf-8"), self._salt, self._hash_iterations
        )

    @staticmethod
    def _digest_token(value: str) -> bytes:
        return hashlib.sha256(value.encode("ascii")).digest()

    def create(
        self, supplied_secret: str, *, now: datetime | None = None
    ) -> tuple[str, datetime] | None:
        candidate = self._digest_secret(supplied_secret)
        if not hmac.compare_digest(candidate, self._expected):
            return None
        current = (now or datetime.now(UTC)).astimezone(UTC)
        token = secrets.token_urlsafe(32)
        expires = current + self._ttl
        self._sessions[self._digest_token(token)] = expires
        self._discard_expired(current)
        while len(self._sessions) > self._max_sessions:
            self._sessions.pop(next(iter(self._sessions)))
        return token, expires

    def authenticate(
        self, token: str | None, *, now: datetime | None = None
    ) -> SessionIdentity | None:
        if not token:
            return None
        current = (now or datetime.now(UTC)).astimezone(UTC)
        digest = self._digest_token(token)
        expires = self._sessions.get(digest)
        if expires is None or expires <= current:
            self._sessions.pop(digest, None)
            return None
        return SessionIdentity(expires)

    def revoke(self, token: str | None) -> None:
        if token:
            self._sessions.pop(self._digest_token(token), None)

    def _discard_expired(self, now: datetime) -> None:
        expired = [digest for digest, expires in self._sessions.items() if expires <= now]
        for digest in expired:
            self._sessions.pop(digest, None)
