"""Graded decision and enforcement adapter boundaries."""

from .adapters import (
    ActionAdapter,
    ActionOutcome,
    ActionStatus,
    CallbackActionAdapter,
    ContinueAdapter,
    EnforcementCoordinator,
    EnforcementNotice,
    VerificationRecord,
)
from .challenge import (
    RECOVERY_PREFIX,
    SCHEDULED_PREFIX,
    ChallengeCredential,
    ChallengeError,
    ChallengeNotConfigured,
    ChallengeService,
    InvalidChallengeSetup,
    PendingChallenge,
    ResponseOutcome,
    ResponseResult,
    UnknownChallenge,
    is_engine_decision,
    is_recovery_challenge,
    is_scheduled_challenge,
)
from .config import EnforcementSettings, load_enforcement_settings
from .native import NativeChallengeAdapter, WindowsLockAdapter
from .policy import DecisionPolicy, PolicyResult
from .session import (
    BLOCKING_POSTURES,
    EnforcementPosture,
    EnforcementSessionState,
    PostureTransition,
)

__all__ = [
    "BLOCKING_POSTURES",
    "RECOVERY_PREFIX",
    "SCHEDULED_PREFIX",
    "ActionAdapter",
    "ActionOutcome",
    "ActionStatus",
    "CallbackActionAdapter",
    "ChallengeCredential",
    "ChallengeError",
    "ChallengeNotConfigured",
    "ChallengeService",
    "ContinueAdapter",
    "DecisionPolicy",
    "EnforcementCoordinator",
    "EnforcementNotice",
    "EnforcementPosture",
    "EnforcementSessionState",
    "EnforcementSettings",
    "InvalidChallengeSetup",
    "NativeChallengeAdapter",
    "PendingChallenge",
    "PolicyResult",
    "PostureTransition",
    "ResponseOutcome",
    "ResponseResult",
    "UnknownChallenge",
    "VerificationRecord",
    "WindowsLockAdapter",
    "is_engine_decision",
    "is_recovery_challenge",
    "is_scheduled_challenge",
    "load_enforcement_settings",
]
