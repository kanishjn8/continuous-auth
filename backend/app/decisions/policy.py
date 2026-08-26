"""Escalation ladder, cooldown, and action budget."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from protocol.generated.python.contracts import DecisionAction, RiskLevel

if TYPE_CHECKING:
    from backend.app.risk.config import RiskSettings


@dataclass(frozen=True)
class PolicyResult:
    requested: DecisionAction
    emitted: DecisionAction
    enforcement_applied: bool
    reason_code: str
    budget_exhausted: bool


class DecisionPolicy:
    def __init__(self, settings: RiskSettings):
        self._settings = settings
        self._last_by_action: dict[DecisionAction, int] = {}
        self._applied_times = deque[int]()

    def _requested(self, level: RiskLevel, high_count: int) -> DecisionAction:
        if level == RiskLevel.LOW:
            return DecisionAction.CONTINUE
        if level == RiskLevel.MEDIUM:
            return DecisionAction.SOFT_CHALLENGE
        if level == RiskLevel.HIGH:
            if high_count >= self._settings.risk.breach_n:
                return DecisionAction.TERMINATE
            return DecisionAction.REAUTH
        return DecisionAction.NONE

    def decide(
        self,
        *,
        level: RiskLevel,
        high_count: int,
        t_decision_us: int,
        enforcement_enabled: bool,
    ) -> PolicyResult:
        requested = self._requested(level, high_count)
        if not enforcement_enabled or requested in (DecisionAction.CONTINUE, DecisionAction.NONE):
            return PolicyResult(
                requested=requested,
                emitted=requested,
                enforcement_applied=False,
                reason_code="SHADOW_OR_NON_ENFORCING" if not enforcement_enabled else "RISK_LOW",
                budget_exhausted=False,
            )

        window_start = t_decision_us - self._settings.cooldown_us
        while self._applied_times and self._applied_times[0] <= window_start:
            self._applied_times.popleft()
        last_same = self._last_by_action.get(requested)
        if last_same is not None and t_decision_us - last_same < self._settings.cooldown_us:
            return PolicyResult(requested, DecisionAction.NONE, False, "ACTION_COOLDOWN", False)
        if len(self._applied_times) >= self._settings.risk.action_budget:
            return PolicyResult(requested, DecisionAction.NONE, False, "ACTION_BUDGET", True)

        self._last_by_action[requested] = t_decision_us
        self._applied_times.append(t_decision_us)
        return PolicyResult(requested, requested, True, "RISK_ESCALATION", False)
