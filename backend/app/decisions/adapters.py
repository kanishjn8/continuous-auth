"""Fail-open enforcement adapters and independently evidenced anchors for T-014."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from protocol.generated.python.contracts import (
    DecisionAction,
    RiskDecision,
    UserState,
    VerificationAnchor,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ActionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED_OPEN = "FAILED_OPEN"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class AdapterResult:
    status: ActionStatus
    code: str
    evidence_reference: str | None = None


@dataclass(frozen=True)
class ActionRequest:
    decision: RiskDecision
    correlation_id: str


class ActionAdapter(Protocol):
    action: DecisionAction

    def execute(self, request: ActionRequest) -> AdapterResult: ...


class ContinueAdapter:
    action = DecisionAction.CONTINUE

    def execute(self, request: ActionRequest) -> AdapterResult:
        del request
        return AdapterResult(ActionStatus.SUCCEEDED, "CONTINUE_OBSERVED")


class CallbackActionAdapter:
    """Wrap a reviewed local OS/UI mechanism without coupling policy to the dashboard."""

    def __init__(
        self,
        action: DecisionAction,
        callback: Callable[[ActionRequest], AdapterResult],
    ) -> None:
        if action not in {
            DecisionAction.SOFT_CHALLENGE,
            DecisionAction.REAUTH,
            DecisionAction.TERMINATE,
        }:
            raise ValueError("callback adapter requires an enforcing action")
        self.action = action
        self._callback = callback

    def execute(self, request: ActionRequest) -> AdapterResult:
        return self._callback(request)


@dataclass(frozen=True)
class VerificationRecord:
    anchor_id: str
    user_id: str
    session_id: str
    segment_id: str | None
    anchor_type: VerificationAnchor
    evidence_reference: str
    authenticated_at: datetime


@dataclass(frozen=True)
class ActionOutcome:
    decision_id: str
    requested_action: DecisionAction
    status: ActionStatus
    code: str
    occurred_at: datetime
    verification: VerificationRecord | None


@dataclass(frozen=True)
class EnforcementNotice:
    severity: str
    code: str
    decision_id: str


class EnforcementStore(Protocol):
    def record_action_outcome(self, outcome: ActionOutcome) -> None: ...

    def record_verification(self, verification: VerificationRecord) -> None: ...


NoticeSink = Callable[[EnforcementNotice], None]
IdFactory = Callable[[], str]


def _new_anchor_id() -> str:
    return f"anchor-{uuid.uuid4()}"


def _evidence_digest(reference: str) -> str:
    """Persist only an opaque proof digest, never a credential or challenge response."""

    if not reference.strip():
        raise ValueError("verification evidence reference must not be blank")
    return hashlib.sha256(reference.encode("utf-8")).hexdigest()


class EnforcementCoordinator:
    """Execute state-gated actions; every infrastructure failure remains fail-open."""

    def __init__(
        self,
        adapters: Mapping[DecisionAction, ActionAdapter],
        *,
        store: EnforcementStore | None = None,
        notice_sink: NoticeSink | None = None,
        anchor_id_factory: IdFactory | None = None,
    ) -> None:
        self._adapters = dict(adapters)
        self._adapters.setdefault(DecisionAction.CONTINUE, ContinueAdapter())
        self._store = store
        self._notice_sink = notice_sink
        self._anchor_id_factory = anchor_id_factory or _new_anchor_id

    def _record(self, outcome: ActionOutcome) -> ActionOutcome:
        if self._store is not None:
            if outcome.verification is not None:
                self._store.record_verification(outcome.verification)
            self._store.record_action_outcome(outcome)
        return outcome

    def record_authenticated_entry(
        self,
        *,
        user_id: str,
        session_id: str,
        segment_id: str | None,
        evidence_reference: str,
        authenticated_at: datetime | None = None,
    ) -> VerificationRecord:
        verification = VerificationRecord(
            anchor_id=self._anchor_id_factory(),
            user_id=user_id,
            session_id=session_id,
            segment_id=segment_id,
            anchor_type=VerificationAnchor.A1_LOGIN_UNLOCK,
            evidence_reference=_evidence_digest(evidence_reference),
            authenticated_at=authenticated_at or _utc_now(),
        )
        if self._store is not None:
            self._store.record_verification(verification)
        return verification

    def execute(self, decision: RiskDecision) -> ActionOutcome:
        now = _utc_now()
        if decision.user_state is not UserState.ACTIVE or not decision.enforcement_applied:
            return self._record(
                ActionOutcome(
                    decision.decision_id,
                    decision.action,
                    ActionStatus.SKIPPED,
                    "STATE_GATED_NO_ENFORCEMENT",
                    now,
                    None,
                )
            )
        adapter = self._adapters.get(decision.action)
        if adapter is None:
            return self._fail_open(decision, "ENFORCEMENT_ADAPTER_UNAVAILABLE", now)
        try:
            result = adapter.execute(ActionRequest(decision, decision.decision_id))
        except Exception:
            return self._fail_open(decision, "ENFORCEMENT_ADAPTER_FAILED", now)

        verification: VerificationRecord | None = None
        if (
            decision.action is DecisionAction.REAUTH
            and result.status is ActionStatus.SUCCEEDED
            and result.evidence_reference is not None
        ):
            verification = VerificationRecord(
                anchor_id=self._anchor_id_factory(),
                user_id=decision.user_id,
                session_id=decision.session_id,
                segment_id=decision.segment_id,
                anchor_type=VerificationAnchor.A2_REAUTH,
                evidence_reference=_evidence_digest(result.evidence_reference),
                authenticated_at=now,
            )
        return self._record(
            ActionOutcome(
                decision.decision_id,
                decision.action,
                result.status,
                result.code,
                now,
                verification,
            )
        )

    def record_scheduled_verification(
        self,
        *,
        user_id: str,
        session_id: str,
        segment_id: str,
        result: AdapterResult,
        authenticated_at: datetime | None = None,
    ) -> VerificationRecord | None:
        """Create A3 only for an explicit successful periodic prompt."""

        if result.status is not ActionStatus.SUCCEEDED or result.evidence_reference is None:
            return None
        verification = VerificationRecord(
            anchor_id=self._anchor_id_factory(),
            user_id=user_id,
            session_id=session_id,
            segment_id=segment_id,
            anchor_type=VerificationAnchor.A3_SCHEDULED_PROMPT,
            evidence_reference=_evidence_digest(result.evidence_reference),
            authenticated_at=authenticated_at or _utc_now(),
        )
        if self._store is not None:
            self._store.record_verification(verification)
        return verification

    def _fail_open(
        self,
        decision: RiskDecision,
        code: str,
        occurred_at: datetime,
    ) -> ActionOutcome:
        notice = EnforcementNotice("HIGH", code, decision.decision_id)
        if self._notice_sink is not None:
            self._notice_sink(notice)
        return self._record(
            ActionOutcome(
                decision.decision_id,
                decision.action,
                ActionStatus.FAILED_OPEN,
                code,
                occurred_at,
                None,
            )
        )
