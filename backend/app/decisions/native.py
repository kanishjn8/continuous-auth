"""Windows-native enforcement: the always-on-top prompt and the real lock.

PLAN.md Section 5.2 states the dashboard must not be the only enforcement
path, and Section 14.1 names the threat as hijack of an unlocked workstation.
Enforcement therefore acts on the workstation itself: an always-on-top native
prompt for ``SOFT_CHALLENGE``/``REAUTH``, and ``LockWorkStation`` for
``TERMINATE``.

Two guards apply to every action here:

* ``EnforcementSettings.enabled`` is false by default, so ordinary collection
  and calibration never lock a participant out on a false positive.
* Nothing Windows-specific is attempted off Windows. A Linux container cannot
  lock the Windows host or draw on its desktop, so those paths report
  ``SKIPPED`` rather than failing.

Dispatch is always non-blocking: the prompt runs in its own process and the
answer returns asynchronously through the C7 response endpoint.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass

from protocol.generated.python.contracts import DecisionAction

from .adapters import ActionRequest, ActionStatus, AdapterResult
from .challenge import CHALLENGE_ACTIONS, ChallengeError, ChallengeService
from .config import EnforcementSettings

LOGGER = logging.getLogger(__name__)

PROMPT_MODULE = "backend.app.decisions.prompt"


def windows_platform() -> bool:
    return sys.platform == "win32"


def lock_workstation() -> bool:
    """Lock the real Windows workstation. Returns False when unavailable."""

    if not windows_platform():
        return False
    try:
        import ctypes

        # Reached via getattr so this module still type-checks on platforms
        # where `ctypes.windll` does not exist.
        user32 = getattr(ctypes, "windll").user32  # noqa: B009
        return bool(user32.LockWorkStation())
    except (AttributeError, OSError):
        LOGGER.exception("LockWorkStation call failed")
        return False


@dataclass(frozen=True)
class PromptDispatch:
    """How a dispatched native prompt reaches the response endpoint."""

    endpoint: str
    decision_id: str
    question: str
    token: str
    blocking: bool


def spawn_prompt(dispatch: PromptDispatch) -> subprocess.Popen[bytes] | None:
    """Start the prompt process and return immediately.

    The one-time response token travels in the environment rather than argv so
    it does not appear in a process listing. The answer itself never returns
    through this process boundary -- the prompt posts it straight to the
    response endpoint.
    """

    if not windows_platform():
        return None
    environment = dict(os.environ)
    environment.update(
        {
            "CA_CHALLENGE_ENDPOINT": dispatch.endpoint,
            "CA_CHALLENGE_DECISION_ID": dispatch.decision_id,
            "CA_CHALLENGE_QUESTION": dispatch.question,
            "CA_CHALLENGE_TOKEN": dispatch.token,
            "CA_CHALLENGE_BLOCKING": "1" if dispatch.blocking else "0",
        }
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.Popen(  # noqa: S603 - fixed module, no shell, no user input in argv
            [sys.executable, "-m", PROMPT_MODULE],
            env=environment,
            creationflags=creation_flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        LOGGER.exception("native challenge prompt could not be started")
        return None


class NativeChallengeAdapter:
    """Dispatch a native prompt for ``SOFT_CHALLENGE`` or ``REAUTH``.

    ``execute`` registers the pending challenge and starts the prompt process,
    then returns. It never waits for the human answer, so the ingestion path
    is never blocked by a person who has walked away.
    """

    def __init__(
        self,
        action: DecisionAction,
        *,
        service: ChallengeService,
        settings: EnforcementSettings,
        response_endpoint: str,
    ) -> None:
        if action not in CHALLENGE_ACTIONS:
            raise ValueError("native challenge adapter requires a challenge action")
        self.action = action
        self._service = service
        self._settings = settings
        self._response_endpoint = response_endpoint.rstrip("/")

    def execute(self, request: ActionRequest) -> AdapterResult:
        try:
            pending = self._service.open(request.decision)
        except ChallengeError:
            LOGGER.warning("challenge requested before a security challenge was configured")
            return AdapterResult(ActionStatus.SKIPPED, "CHALLENGE_NOT_CONFIGURED")
        if not self._settings.prompt_enabled:
            return AdapterResult(ActionStatus.SKIPPED, "ENFORCEMENT_DISABLED")
        if not windows_platform():
            return AdapterResult(ActionStatus.SKIPPED, "NATIVE_PROMPT_UNSUPPORTED_PLATFORM")
        process = spawn_prompt(
            PromptDispatch(
                endpoint=f"{self._response_endpoint}/{pending.decision_id}/respond",
                decision_id=pending.decision_id,
                question=pending.question,
                token=pending.response_token,
                blocking=pending.blocking,
            )
        )
        if process is None:
            self._service.discard(pending.decision_id)
            return AdapterResult(ActionStatus.FAILED_OPEN, "NATIVE_PROMPT_UNAVAILABLE")
        return AdapterResult(ActionStatus.SUCCEEDED, "CHALLENGE_DISPATCHED")


class WindowsLockAdapter:
    """Lock the real workstation for ``TERMINATE``."""

    action = DecisionAction.TERMINATE

    def __init__(self, settings: EnforcementSettings) -> None:
        self._settings = settings

    def execute(self, request: ActionRequest) -> AdapterResult:
        del request
        if not self._settings.workstation_lock_enabled:
            return AdapterResult(ActionStatus.SKIPPED, "ENFORCEMENT_DISABLED")
        if not windows_platform():
            return AdapterResult(ActionStatus.SKIPPED, "WORKSTATION_LOCK_UNSUPPORTED_PLATFORM")
        if not lock_workstation():
            return AdapterResult(ActionStatus.FAILED_OPEN, "WORKSTATION_LOCK_FAILED")
        return AdapterResult(ActionStatus.SUCCEEDED, "WORKSTATION_LOCKED")
