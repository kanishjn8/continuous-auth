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
    """Every piece of evidence required to admit a user's first profile.

    ``participant_id`` binds this admission object to exactly one
    participant. A caller that loops over multiple users (e.g.
    ``ml.evaluation.pipeline``) but forwards the same ``EnrollmentAdmission``
    instance to every trainer call relies on ``require_enrollment_admission``
    rejecting a mismatched ``user_id`` rather than on incidental behaviour
    elsewhere (e.g. a consent-record lookup happening to fail first).
    """

    settings: CollectionSettings
    consent: ConsentRecord | None
    enrollment: EnrollmentRecord | None
    manifest_window_ids: frozenset[str]
    observed_at_by_window: Mapping[str, datetime]
    #: Scoped to the frozen TRAIN partition, not to the whole corpus and not
    #: to the user's live history. The live state machine's own thresholds
    #: live in ``config/risk.*.yaml -> enrollment.*`` and answer a different
    #: question ("how much has the runtime observed before this user stops
    #: being ENROLLING?"). Conflating the two silently turned a "3 distinct
    #: days" rule into an undocumented "6 collection days" requirement,
    #: because a 60/20/20 day-disjoint freeze gives TRAIN roughly half the
    #: collected days. These come from ``config/ml.*.yaml -> enrollment.*``
    #: (ADR-014).
    min_train_windows: int
    min_train_distinct_days: int
    user_has_active_profile: bool
    participant_id: str


def require_enrollment_admission(
    user_id: str,
    windows: Sequence[FeatureWindow],
    admission: EnrollmentAdmission,
) -> None:
    """Validate that a user's first profile may be trained on these windows."""

    if admission.participant_id != user_id:
        raise EnrollmentAdmissionError(
            f"PARTICIPANT_MISMATCH: admission is bound to participant "
            f"{admission.participant_id!r}, not {user_id!r}; a single "
            "EnrollmentAdmission must never be reused across participants"
        )

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
                f"{','.join(reasons)}: window {window.window_id!r} is not " "evaluation-eligible"
            )

    if len(windows) < admission.min_train_windows:
        raise EnrollmentAdmissionError(
            f"INSUFFICIENT_WINDOWS: {len(windows)} TRAIN-partition windows < "
            f"min_train_windows={admission.min_train_windows}"
        )
    distinct_days = {window.collection_day for window in windows}
    if len(distinct_days) < admission.min_train_distinct_days:
        raise EnrollmentAdmissionError(
            f"INSUFFICIENT_DISTINCT_DAYS: {len(distinct_days)} distinct TRAIN-partition "
            f"days < min_train_distinct_days={admission.min_train_distinct_days}"
        )
