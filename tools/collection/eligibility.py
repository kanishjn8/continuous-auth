"""Consent, enrollment, provenance, and scheduled-verification eligibility rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from protocol.generated.python.contracts import DataProvenance

from .config import CollectionSettings

_PSEUDONYM = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("collection timestamps must include a timezone")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ConsentRecord:
    participant_id: str
    protocol_revision: str
    consented_at: datetime
    withdrawn_at: datetime | None = None

    def __post_init__(self) -> None:
        if not _PSEUDONYM.fullmatch(self.participant_id):
            raise ValueError("participant_id must be a pseudonymous identifier")
        _as_utc(self.consented_at)
        if self.withdrawn_at is not None and _as_utc(self.withdrawn_at) < _as_utc(
            self.consented_at
        ):
            raise ValueError("withdrawal cannot precede consent")

    def active_at(self, observed_at: datetime) -> bool:
        instant = _as_utc(observed_at)
        return _as_utc(self.consented_at) <= instant and (
            self.withdrawn_at is None or instant < _as_utc(self.withdrawn_at)
        )


@dataclass(frozen=True)
class EnrollmentRecord:
    participant_id: str
    enrolled_at: datetime
    collector_version: str
    protocol_version: str

    def __post_init__(self) -> None:
        if not _PSEUDONYM.fullmatch(self.participant_id):
            raise ValueError("participant_id must be a pseudonymous identifier")
        _as_utc(self.enrolled_at)
        if not self.collector_version or not self.protocol_version:
            raise ValueError("enrollment versions are required")


def eligibility_reasons(
    *,
    participant_id: str,
    provenance: DataProvenance,
    observed_at: datetime,
    consent: ConsentRecord | None,
    enrollment: EnrollmentRecord | None,
    settings: CollectionSettings,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if consent is None or consent.participant_id != participant_id:
        reasons.append("MISSING_CONSENT")
    elif not consent.active_at(observed_at):
        reasons.append("CONSENT_NOT_ACTIVE")
    if enrollment is None or enrollment.participant_id != participant_id:
        reasons.append("MISSING_ENROLLMENT")
    elif _as_utc(observed_at) < _as_utc(enrollment.enrolled_at):
        reasons.append("BEFORE_ENROLLMENT")
    if provenance not in settings.eligible_provenance:
        reasons.append("INELIGIBLE_PROVENANCE")
    return tuple(reasons)


def next_scheduled_anchor_due(
    last_successful_anchor: datetime,
    settings: CollectionSettings,
) -> datetime:
    """Return an A3 due time; this schedules evidence but never fabricates it."""

    return _as_utc(last_successful_anchor) + timedelta(
        hours=settings.scheduled_anchor_interval_hours
    )
