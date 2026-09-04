"""First-profile training-data admission boundary (ADR-013).

PLAN.md Section 12 is an *update* gate: it admits new data into the training
set of a profile that already exists. Gate G3 counts scored windows, and
scoring requires an active model, so no first profile can ever satisfy it.
PLAN.md Section 5.3 treats the first model as an enrollment transition, not a
promotion.

This module is that enrollment boundary. It is not weaker than the promotion
gate, it is evidence of a different kind: consent, enrollment, and corpus
integrity rather than risk-and-verification evidence that cannot exist before
a model does. It applies to a user's FIRST profile only -- once a profile is
active this gate refuses, and `ml.training.gate.require_promotion_gate` is the
only remaining route.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from ml.features.schema import FeatureWindow
from tools.collection.config import CollectionSettings
from tools.collection.eligibility import (
    ConsentRecord,
    EnrollmentRecord,
    eligibility_reasons,
)


class EnrollmentAdmissionError(ValueError):
    """Raised when first-profile training data lacks complete admission evidence."""


@dataclass(frozen=True)
class EnrollmentAdmission:
    """Every piece of evidence required to admit a user's first profile."""

    settings: CollectionSettings
    consent: ConsentRecord | None
    enrollment: EnrollmentRecord | None
    manifest_window_ids: frozenset[str]
    observed_at_by_window: Mapping[str, datetime]
    min_windows: int
    min_distinct_days: int
    user_has_active_profile: bool


def require_enrollment_admission(
    user_id: str,
    windows: Sequence[FeatureWindow],
    admission: EnrollmentAdmission,
) -> None:
    """Validate that a user's first profile may be trained on these windows."""

    if admission.user_has_active_profile:
        raise EnrollmentAdmissionError(
            f"PROFILE_ALREADY_ACTIVE: user {user_id!r} has an active profile; "
            "further training data must pass the Model Update Manager promotion gate"
        )

    for window in windows:
        if window.user_id != user_id:
            raise EnrollmentAdmissionError(
                f"FOREIGN_USER_WINDOW: window {window.window_id!r} belongs to another user"
            )
        if window.window_id not in admission.manifest_window_ids:
            raise EnrollmentAdmissionError(
                f"WINDOW_NOT_IN_FROZEN_CORPUS: window {window.window_id!r} is not "
                "named in the verified freeze manifest"
            )
        observed_at = admission.observed_at_by_window.get(window.window_id)
        if observed_at is None:
            raise EnrollmentAdmissionError(
                f"MISSING_OBSERVATION_TIME: window {window.window_id!r} has no "
                "recorded observation time; it is never defaulted"
            )
        # ml.features.schema.Provenance is protocol.generated.python.contracts
        # .DataProvenance itself (a module-level alias), not a parallel enum
        # with matching values -- verified by identity check before writing
        # this module. eligibility_reasons already expects that exact type,
        # so window.provenance is passed through with no bridging.
        reasons = eligibility_reasons(
            participant_id=user_id,
            provenance=window.provenance,
            observed_at=observed_at,
            consent=admission.consent,
            enrollment=admission.enrollment,
            settings=admission.settings,
        )
        if reasons:
            raise EnrollmentAdmissionError(
                f"{','.join(reasons)}: window {window.window_id!r} is not "
                "evaluation-eligible"
            )

    if len(windows) < admission.min_windows:
        raise EnrollmentAdmissionError(
            f"INSUFFICIENT_WINDOWS: {len(windows)} < {admission.min_windows}"
        )
    distinct_days = {window.collection_day for window in windows}
    if len(distinct_days) < admission.min_distinct_days:
        raise EnrollmentAdmissionError(
            f"INSUFFICIENT_DISTINCT_DAYS: {len(distinct_days)} < "
            f"{admission.min_distinct_days}"
        )
