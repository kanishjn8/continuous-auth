"""Per-user enrollment/calibration/active/degraded/suspended state machine."""

from __future__ import annotations

from dataclasses import dataclass

from protocol.generated.python.contracts import EnrollmentConfig, UserState


@dataclass(frozen=True)
class StateTransition:
    previous: UserState
    current: UserState
    reason: str


class UserStateMachine:
    def __init__(
        self,
        enrollment: EnrollmentConfig,
        initial: UserState = UserState.ENROLLING,
    ):
        self._config = enrollment
        self._state = initial
        self._pre_degraded: UserState | None = None

    @property
    def state(self) -> UserState:
        return self._state

    def _move(self, target: UserState, reason: str) -> StateTransition:
        transition = StateTransition(self._state, target, reason)
        self._state = target
        return transition

    def observe_progress(
        self,
        *,
        enrollment_windows: int,
        distinct_days: int,
        calibration_windows: int,
    ) -> StateTransition | None:
        if min(enrollment_windows, distinct_days, calibration_windows) < 0:
            raise ValueError("state progress counters must be non-negative")
        if self._state == UserState.ENROLLING:
            if (
                enrollment_windows >= self._config.min_windows
                and distinct_days >= self._config.min_distinct_days
            ):
                return self._move(UserState.CALIBRATING, "ENROLLMENT_EVIDENCE_REACHED")
            return None
        if (
            self._state == UserState.CALIBRATING
            and calibration_windows >= self._config.calibration_windows
        ):
            return self._move(UserState.ACTIVE, "CALIBRATION_COMPLETED")
        return None

    def degrade(self, reason: str) -> StateTransition | None:
        if self._state in (UserState.DEGRADED, UserState.SUSPENDED):
            return None
        self._pre_degraded = self._state
        return self._move(UserState.DEGRADED, reason)

    def recover(self) -> StateTransition:
        if self._state != UserState.DEGRADED:
            raise ValueError("only a degraded state can recover")
        target = self._pre_degraded or UserState.ENROLLING
        self._pre_degraded = None
        return self._move(target, "COMPONENT_RECOVERED")

    def suspend(self, reason: str) -> StateTransition | None:
        if self._state == UserState.SUSPENDED:
            return None
        self._pre_degraded = None
        return self._move(UserState.SUSPENDED, reason)

    def restart_enrollment(self) -> StateTransition:
        if self._state != UserState.SUSPENDED:
            raise ValueError("only a suspended profile can restart enrollment")
        return self._move(UserState.ENROLLING, "REENROLLED")
