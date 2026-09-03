"""Participant-configured security challenge and its asynchronous lifecycle.

The escalation ladder (PLAN.md Section 11.3) requests `SOFT_CHALLENGE` or
`REAUTH`, but a human answering a prompt is asynchronous while
``ActionAdapter.execute`` is synchronous and runs on the ingestion path. This
module resolves that: :meth:`ChallengeService.open` only registers a pending
challenge in memory and returns immediately, and the answer arrives later
through the C7 response endpoint.

The question and answer are chosen by the participant, never shipped as a
default. They are a credential, so they are persisted to the ignored local
database (AGENTS.md constraint 8) as a PBKDF2-HMAC-SHA256 digest with a
per-credential salt. The plaintext answer is compared and discarded; it is
never stored, logged, audited, or returned through any API or WebSocket.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from protocol.generated.python.contracts import DecisionAction, RiskDecision

from .config import EnforcementSettings

if TYPE_CHECKING:
    from backend.app.storage.service import StorageService

_SALT_BYTES = 16
_DOCUMENT_VERSION = 1
_MIN_QUESTION_LENGTH = 4
_MIN_ANSWER_LENGTH = 4

CHALLENGE_ACTIONS = frozenset({DecisionAction.SOFT_CHALLENGE, DecisionAction.REAUTH})

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ChallengeError(ValueError):
    """Base class for challenge configuration and response failures."""


class ChallengeNotConfigured(ChallengeError):
    pass


class ChallengeAlreadyConfigured(ChallengeError):
    pass


class InvalidChallengeSetup(ChallengeError):
    pass


class UnknownChallenge(ChallengeError):
    pass


class ResponseOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class ChallengeCredential:
    """A participant-chosen question with a salted digest of its answer."""

    question: str
    salt_hex: str = field(repr=False)
    digest_hex: str = field(repr=False)
    iterations: int

    def verify(self, answer: str) -> bool:
        candidate = _derive(answer, bytes.fromhex(self.salt_hex), self.iterations)
        return hmac.compare_digest(candidate, bytes.fromhex(self.digest_hex))

    def serialise(self) -> str:
        return json.dumps(
            {
                "version": _DOCUMENT_VERSION,
                "question": self.question,
                "salt": self.salt_hex,
                "digest": self.digest_hex,
                "iterations": self.iterations,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def deserialise(cls, document: str) -> ChallengeCredential:
        try:
            parsed = json.loads(document)
            version = int(parsed["version"])
            if version != _DOCUMENT_VERSION:
                raise ChallengeError(f"unsupported challenge document version {version}")
            return cls(
                question=str(parsed["question"]),
                salt_hex=str(parsed["salt"]),
                digest_hex=str(parsed["digest"]),
                iterations=int(parsed["iterations"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ChallengeError):
                raise
            raise ChallengeError("stored security challenge is unreadable") from exc


def _derive(answer: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", answer.encode("utf-8"), salt, iterations)


@dataclass(frozen=True)
class PendingChallenge:
    """A dispatched challenge awaiting a human response."""

    decision_id: str
    user_id: str
    session_id: str
    segment_id: str
    action: DecisionAction
    question: str
    opened_at: datetime
    expires_at: datetime
    response_token: str = field(repr=False, default="")

    @property
    def blocking(self) -> bool:
        """REAUTH must be answered before work continues; SOFT_CHALLENGE need not."""

        return self.action is DecisionAction.REAUTH


@dataclass(frozen=True)
class ResponseResult:
    outcome: ResponseOutcome
    challenge: PendingChallenge
    evidence_reference: str | None = None


class ChallengeService:
    """Own the stored credential and the in-memory pending-challenge registry.

    Every method is safe to call from the ingestion thread and the API event
    loop concurrently. :meth:`open` performs no I/O and never blocks.
    """

    def __init__(
        self,
        storage: StorageService,
        settings: EnforcementSettings,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._storage = storage
        self._settings = settings
        self._clock = clock or _utc_now
        self._lock = threading.Lock()
        self._pending: dict[str, PendingChallenge] = {}
        self._credential: ChallengeCredential | None = None
        self._loaded = False

    # -- configuration -------------------------------------------------

    def _credential_or_none(self) -> ChallengeCredential | None:
        with self._lock:
            if not self._loaded:
                document = self._storage.security_challenge_document()
                self._credential = (
                    ChallengeCredential.deserialise(document) if document is not None else None
                )
                self._loaded = True
            return self._credential

    def is_configured(self) -> bool:
        """True once the participant has completed first-run setup."""

        return self._credential_or_none() is not None

    def question(self) -> str | None:
        credential = self._credential_or_none()
        return credential.question if credential is not None else None

    def configure(
        self,
        *,
        question: str,
        answer: str,
        confirm_answer: str,
        current_answer: str | None = None,
    ) -> None:
        """Set the first challenge, or rotate an existing one.

        Rotation requires the current answer. The dashboard is co-located with
        the monitored endpoint (PLAN.md 14.4), so without this an impostor
        occupying the session could simply replace the challenge and walk
        through the next escalation.
        """

        question = question.strip()
        if len(question) < _MIN_QUESTION_LENGTH:
            raise InvalidChallengeSetup("security question is too short")
        if len(answer) < _MIN_ANSWER_LENGTH:
            raise InvalidChallengeSetup("security answer is too short")
        if answer != confirm_answer:
            raise InvalidChallengeSetup("security answers do not match")

        existing = self._credential_or_none()
        if existing is not None:
            if current_answer is None:
                raise ChallengeAlreadyConfigured(
                    "changing the security challenge requires the current answer"
                )
            if not existing.verify(current_answer):
                raise InvalidChallengeSetup("current security answer is incorrect")

        salt = secrets.token_bytes(_SALT_BYTES)
        iterations = self._settings.answer_hash_iterations
        credential = ChallengeCredential(
            question=question,
            salt_hex=salt.hex(),
            digest_hex=_derive(answer, salt, iterations).hex(),
            iterations=iterations,
        )
        self._storage.set_security_challenge_document(credential.serialise())
        with self._lock:
            self._credential = credential
            self._loaded = True

    # -- dispatch ------------------------------------------------------

    def open(self, decision: RiskDecision) -> PendingChallenge:
        """Register a pending challenge and return immediately.

        In-memory only: no database access, no network, no waiting on a human.
        This is what keeps ``ActionAdapter.execute`` non-blocking on the
        ingestion path.
        """

        credential = self._credential_or_none()
        if credential is None:
            raise ChallengeNotConfigured("no security challenge has been configured")
        now = self._clock()
        pending = PendingChallenge(
            decision_id=decision.decision_id,
            user_id=decision.user_id,
            session_id=decision.session_id,
            segment_id=decision.segment_id,
            action=decision.action,
            question=credential.question,
            opened_at=now,
            expires_at=now + timedelta(seconds=self._settings.challenge_timeout_seconds),
            response_token=secrets.token_urlsafe(32),
        )
        with self._lock:
            self._pending[pending.decision_id] = pending
        return pending

    def open_scheduled(
        self,
        *,
        user_id: str,
        session_id: str,
        segment_id: str,
    ) -> PendingChallenge:
        """Register a pending A3 scheduled verification prompt.

        Deliberately parallel to :meth:`open` rather than routed through
        ``EnforcementCoordinator``. ``config/enforcement.development.yaml``
        sets ``enabled: false`` for ordinary collection, so
        ``NativeChallengeAdapter`` would return ``ENFORCEMENT_DISABLED`` and no
        prompt would ever appear during a pilot. Enforcement policy governs
        whether the system acts against a participant; it must not govern
        whether research evidence can be created.

        A scheduled verification is not a risk decision, so no ``RiskDecision``
        is accepted or fabricated. It is non-blocking: a verification prompt
        must never stop a participant working.
        """

        credential = self._credential_or_none()
        if credential is None:
            raise ChallengeNotConfigured("no security challenge has been configured")
        now = self._clock()
        pending = PendingChallenge(
            decision_id=f"scheduled-anchor:{secrets.token_urlsafe(16)}",
            user_id=user_id,
            session_id=session_id,
            segment_id=segment_id,
            action=DecisionAction.SOFT_CHALLENGE,
            question=credential.question,
            opened_at=now,
            expires_at=now + timedelta(seconds=self._settings.challenge_timeout_seconds),
            response_token=secrets.token_urlsafe(32),
        )
        with self._lock:
            self._pending[pending.decision_id] = pending
        return pending

    def pending(self) -> tuple[PendingChallenge, ...]:
        now = self._clock()
        with self._lock:
            return tuple(item for item in self._pending.values() if item.expires_at > now)

    def active_challenge(self) -> PendingChallenge | None:
        """The most recent unexpired challenge, for dashboard observability."""

        outstanding = self.pending()
        if not outstanding:
            return None
        return max(outstanding, key=lambda item: item.opened_at)

    # -- response ------------------------------------------------------

    def respond(
        self,
        decision_id: str,
        answer: str,
        *,
        response_token: str | None = None,
    ) -> ResponseResult:
        """Resolve a dispatched challenge with a human answer.

        A wrong answer and an expired challenge are both failures; PLAN.md
        Section 11.3 escalates a failed reauthentication to ``TERMINATE``.

        ``response_token`` authenticates the native prompt, which has no
        dashboard session. Callers that already hold an authenticated session
        pass ``None``. An incorrect token is rejected as an unknown challenge
        so it reveals nothing about which decisions are outstanding.
        """

        credential = self._credential_or_none()
        if credential is None:
            raise ChallengeNotConfigured("no security challenge has been configured")
        with self._lock:
            candidate = self._pending.get(decision_id)
            if candidate is None:
                raise UnknownChallenge("no challenge is pending for that decision")
            if response_token is not None and not hmac.compare_digest(
                candidate.response_token, response_token
            ):
                raise UnknownChallenge("no challenge is pending for that decision")
            pending = self._pending.pop(decision_id)
        if self._clock() > pending.expires_at:
            return ResponseResult(ResponseOutcome.EXPIRED, pending)
        if not credential.verify(answer):
            return ResponseResult(ResponseOutcome.REJECTED, pending)
        return ResponseResult(
            ResponseOutcome.ACCEPTED,
            pending,
            evidence_reference=f"challenge:{pending.decision_id}:{secrets.token_urlsafe(16)}",
        )

    def discard(self, decision_id: str) -> None:
        with self._lock:
            self._pending.pop(decision_id, None)
