"""Authoritative, backend-held enforcement posture for the live session.

PLAN.md Section 5.2 requires that the dashboard "must not be the only
enforcement path", and Section 11.3 defines the ladder
``CONTINUE -> SOFT_CHALLENGE -> REAUTH -> TERMINATE``, escalating to
``TERMINATE`` on "HIGH sustained beyond escalation window, **or failed
reauth**".

``NativeChallengeAdapter`` and ``WindowsLockAdapter`` deliver the OS-level
half of that ladder. This module holds the other half: the process-local
posture that the C7 API consults before serving any protected resource, so a
page refresh, a WebSocket reconnect, a second browser tab, or a direct
``curl`` cannot walk past an outstanding reauthentication or a lockout. It
exists because the OS-level half is Windows-only, and because a workstation
lock tells the backend nothing about whether the session is still trusted.

This module makes no risk judgement. It reads no score, holds no threshold,
and never recomputes a level: posture is derived only from actions the policy
layer already applied and from challenge outcomes the credential already
verified. Nothing here can escalate on its own.

``enabled`` mirrors ``EnforcementSettings.enabled`` for exactly the reason
that flag exists (``config/enforcement.development.yaml``): with enforcement
off, decisions are still computed, recorded and streamed, but nothing acts
against the participant. A false positive during ordinary collection must not
lock the operator out of their own console.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from protocol.generated.python.contracts import DecisionAction

from .challenge import ResponseOutcome


def _utc_now() -> datetime:
    return datetime.now(UTC)


class EnforcementPosture(StrEnum):
    """Where this session sits on the escalation ladder, right now."""

    NORMAL = "NORMAL"
    SOFT_CHALLENGE = "SOFT_CHALLENGE"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    LOCKED_OUT = "LOCKED_OUT"


# A soft challenge is deliberately non-blocking -- that is what makes it
# "soft", and it is the same distinction PendingChallenge.blocking already
# draws. Work continues while one is outstanding; failing it is what costs
# the participant access.
BLOCKING_POSTURES = frozenset({EnforcementPosture.REAUTH_REQUIRED, EnforcementPosture.LOCKED_OUT})

_RANK = {
    EnforcementPosture.NORMAL: 0,
    EnforcementPosture.SOFT_CHALLENGE: 1,
    EnforcementPosture.REAUTH_REQUIRED: 2,
    EnforcementPosture.LOCKED_OUT: 3,
}

_ACTION_POSTURE = {
    DecisionAction.SOFT_CHALLENGE: EnforcementPosture.SOFT_CHALLENGE,
    DecisionAction.REAUTH: EnforcementPosture.REAUTH_REQUIRED,
    DecisionAction.TERMINATE: EnforcementPosture.LOCKED_OUT,
}


@dataclass(frozen=True)
class PostureTransition:
    """One recorded movement along the ladder, for alerting and audit."""

    previous: EnforcementPosture
    current: EnforcementPosture
    code: str
    decision_id: str | None
    occurred_at: datetime


TransitionSink = Callable[[PostureTransition], None]
Clock = Callable[[], datetime]


class EnforcementSessionState:
    """Hold and transition the session posture; safe from any thread.

    The ingestion thread calls :meth:`observe_decision`; the API event loop
    calls :meth:`observe_challenge_outcome` and reads :meth:`snapshot`. Both
    are short critical sections with no I/O inside the lock -- the sink runs
    after the lock is released, so a slow alert write can never stall
    ingestion.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        transition_sink: TransitionSink | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._enabled = enabled
        self._sink = transition_sink
        self._clock = clock or _utc_now
        self._lock = threading.Lock()
        self._posture = EnforcementPosture.NORMAL
        self._decision_id: str | None = None
        self._since: datetime | None = None
        self._failed_responses = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def posture(self) -> EnforcementPosture:
        with self._lock:
            return self._posture

    def blocks_protected_access(self) -> bool:
        """True while an outstanding REAUTH or a lockout must gate the API."""

        with self._lock:
            return self._posture in BLOCKING_POSTURES

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "enforcement_enabled": self._enabled,
                "posture": self._posture.value,
                "blocks_protected_access": self._posture in BLOCKING_POSTURES,
                "locked_out": self._posture is EnforcementPosture.LOCKED_OUT,
                "triggering_decision_id": self._decision_id,
                "since": (
                    None if self._since is None else self._since.isoformat().replace("+00:00", "Z")
                ),
                "failed_responses": self._failed_responses,
            }

    # -- transitions ---------------------------------------------------

    def _move(
        self,
        target: EnforcementPosture,
        code: str,
        decision_id: str | None,
    ) -> PostureTransition | None:
        """Apply a transition under the lock and emit it outside the lock."""

        with self._lock:
            previous = self._posture
            if previous is target:
                return None
            now = self._clock()
            self._posture = target
            self._decision_id = None if target is EnforcementPosture.NORMAL else decision_id
            self._since = None if target is EnforcementPosture.NORMAL else now
            if target is EnforcementPosture.NORMAL:
                self._failed_responses = 0
            transition = PostureTransition(previous, target, code, decision_id, now)
        if self._sink is not None:
            self._sink(transition)
        return transition

    def observe_decision(
        self,
        *,
        decision_id: str,
        action: DecisionAction,
        enforcement_applied: bool,
    ) -> PostureTransition | None:
        """Raise the posture to match an action the policy layer applied.

        Never lowers it: de-escalation is the exclusive business of a
        successfully answered challenge. Ignores actions that are not rungs
        of the ladder, and ignores every decision the policy layer chose not
        to enforce (cooldown, action budget, shadow mode, non-ACTIVE state) --
        the whole point of those suppressions is that nothing acts.

        Posture follows ``enforcement_applied`` rather than the adapter's
        status on purpose. ADR-011 fail-open governs the *OS* action: if the
        native prompt cannot start, no prompt appears and a loud availability
        alert is raised. It does not mean the backend should forget that a
        reauthentication was required -- and off Windows the adapter reports
        ``SKIPPED`` for every action, which would otherwise leave the ladder
        with no enforcement point at all.
        """

        if not self._enabled or not enforcement_applied:
            return None
        target = _ACTION_POSTURE.get(action)
        if target is None:
            return None
        with self._lock:
            if _RANK[target] <= _RANK[self._posture]:
                return None
        return self._move(target, f"ENFORCEMENT_{target.value}", decision_id)

    def observe_challenge_outcome(
        self,
        *,
        decision_id: str,
        action: DecisionAction,
        outcome: ResponseOutcome,
    ) -> PostureTransition | None:
        """Apply the result of a challenge the credential already verified.

        Accepted, per PLAN.md 11.3 read forwards:

        * a correct ``REAUTH`` answer is the explicit reauthentication the
          ladder asks for, and clears everything below it including a
          lockout. It is the only recovery this system has, and it is the
          same evidence that mints the A2 anchor (PLAN.md 12.2).
        * a correct ``SOFT_CHALLENGE`` answer clears a soft challenge only. A
          stale soft answer arriving after the session has already been
          escalated must not unwind that escalation.

        Rejected or expired:

        * a failed ``REAUTH`` is the "failed reauth" clause of the PLAN.md
          11.3 ``TERMINATE`` row -- lockout.
        * a failed ``SOFT_CHALLENGE`` advances exactly one rung, to
          ``REAUTH_REQUIRED``. That is the ladder's next step rather than a
          new policy: the ladder is the source of truth for what follows a
          failed challenge, and jumping straight to lockout would punish a
          mistyped answer far beyond anything the plan describes.

        Scheduled A3 prompts are routine verification, not a risk response,
        and must never reach this method. Callers filter them out by
        decision-id prefix.
        """

        if not self._enabled:
            return None
        target = _ACTION_POSTURE.get(action)
        if target is None or target is EnforcementPosture.LOCKED_OUT:
            return None
        if outcome is ResponseOutcome.ACCEPTED:
            if action is DecisionAction.REAUTH:
                return self._move(
                    EnforcementPosture.NORMAL, "ENFORCEMENT_REAUTH_ACCEPTED", decision_id
                )
            with self._lock:
                if _RANK[self._posture] > _RANK[EnforcementPosture.SOFT_CHALLENGE]:
                    return None
            return self._move(
                EnforcementPosture.NORMAL, "ENFORCEMENT_CHALLENGE_ACCEPTED", decision_id
            )

        with self._lock:
            self._failed_responses += 1
        code = f"ENFORCEMENT_CHALLENGE_{outcome.value}"
        if action is DecisionAction.REAUTH:
            return self._move(EnforcementPosture.LOCKED_OUT, code, decision_id)
        with self._lock:
            if _RANK[self._posture] > _RANK[EnforcementPosture.SOFT_CHALLENGE]:
                return None
        return self._move(EnforcementPosture.REAUTH_REQUIRED, code, decision_id)
