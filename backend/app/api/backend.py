"""Read/admin boundary for C7; raw feature vectors never enter this surface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from sqlite3 import Connection
from typing import Generic, TypeVar

from backend.app.decisions.adapters import EnforcementCoordinator
from backend.app.decisions.challenge import (
    ChallengeError,
    ChallengeService,
    ResponseOutcome,
    UnknownChallenge,
)
from backend.app.decisions.config import EnforcementSettings
from backend.app.storage.service import StorageService
from backend.app.updates.manager import UpdateManager
from backend.app.updates.repository import SQLiteUpdateRepository
from protocol.generated.python.contracts import (
    Alert,
    AlertType,
    CurrentState,
    Health,
    Metrics,
    Profile,
    RiskDecision,
    RiskLevel,
    UpdateCandidate,
    UserState,
)


class ApiBackendError(RuntimeError):
    code = "BACKEND_ERROR"


class ResourceNotFound(ApiBackendError):
    code = "NOT_FOUND"


class ResourceConflict(ApiBackendError):
    code = "CONFLICT"


T = TypeVar("T")


@dataclass(frozen=True)
class PageResult(Generic[T]):
    items: tuple[T, ...]
    has_more: bool


ActiveUserProvider = Callable[[], str | None]
ShadowModeSetter = Callable[[bool], None]


class SQLiteApiBackend:
    def __init__(
        self,
        storage: StorageService,
        *,
        active_user_provider: ActiveUserProvider,
        update_manager: UpdateManager | None = None,
        shadow_mode_setter: ShadowModeSetter | None = None,
        challenge_service: ChallengeService | None = None,
        enforcement: EnforcementCoordinator | None = None,
        enforcement_settings: EnforcementSettings | None = None,
        scheduled_anchor_sink: Callable[..., None] | None = None,
    ) -> None:
        self.storage = storage
        self.active_user_provider = active_user_provider
        self.update_manager = update_manager
        self.shadow_mode_setter = shadow_mode_setter
        self.challenge_service = challenge_service
        self.enforcement = enforcement
        self.enforcement_settings = enforcement_settings
        self._scheduled_anchor_sink = scheduled_anchor_sink
        self.update_repository = SQLiteUpdateRepository(storage)

    def current_state(self) -> CurrentState:
        user_id = self.active_user_provider()
        if user_id is None:
            return CurrentState(
                schema_version="1.0.0",
                user_id=None,
                user_state=UserState.SUSPENDED,
                risk_level=RiskLevel.UNAVAILABLE,
                protection_available=False,
                last_check_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )
        with self.storage.database.connection() as connection:
            user = connection.execute(
                "SELECT state, updated_at_utc FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            if user is None:
                raise ResourceNotFound("the active user has no enrolled profile")
            risk = connection.execute(
                """
                SELECT risk_level, stored_at_utc FROM risk_events
                WHERE user_id = ? ORDER BY t_decision_us DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        state = UserState(user["state"])
        level = RiskLevel(risk["risk_level"]) if risk is not None else RiskLevel.UNAVAILABLE
        shadow_mode = self.storage.shadow_mode_enabled()
        return CurrentState(
            schema_version="1.0.0",
            user_id=user_id,
            user_state=state,
            risk_level=level,
            protection_available=(
                state is UserState.ACTIVE and level is not RiskLevel.UNAVAILABLE and not shadow_mode
            ),
            last_check_at=risk["stored_at_utc"] if risk is not None else user["updated_at_utc"],
        )

    def _profile(self, connection: Connection, user_id: str) -> Profile:
        user = connection.execute(
            "SELECT state FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if user is None:
            raise ResourceNotFound("profile was not found")
        counts = connection.execute(
            """
            SELECT COUNT(*) AS windows, COUNT(DISTINCT collection_day) AS days
            FROM feature_windows WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        model = connection.execute(
            """
            SELECT profile_version FROM model_profiles
            WHERE user_id = ? AND status = 'ACTIVE'
            """,
            (user_id,),
        ).fetchone()
        return Profile(
            user_id=user_id,
            user_state=UserState(user["state"]),
            enrollment_windows=int(counts["windows"]),
            distinct_days=int(counts["days"]),
            model_version=None if model is None else str(model["profile_version"]),
        )

    def get_profile(self, user_id: str) -> Profile:
        with self.storage.database.connection() as connection:
            return self._profile(connection, user_id)

    def create_profile(self, user_id: str) -> Profile:
        with self.storage.database.connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        if exists is not None:
            raise ResourceConflict("profile already exists")
        self.storage.upsert_user(user_id, UserState.ENROLLING)
        return self.get_profile(user_id)

    def list_profiles(self, *, offset: int, limit: int) -> PageResult[Profile]:
        with self.storage.database.connection() as connection:
            rows = connection.execute(
                "SELECT user_id FROM users ORDER BY user_id LIMIT ? OFFSET ?",
                (limit + 1, offset),
            ).fetchall()
            items = tuple(self._profile(connection, str(row["user_id"])) for row in rows[:limit])
        return PageResult(items, len(rows) > limit)

    def list_decisions(
        self,
        *,
        offset: int,
        limit: int,
        user_id: str | None,
    ) -> PageResult[RiskDecision]:
        where = "WHERE user_id = ?" if user_id is not None else ""
        parameters: tuple[object, ...] = (
            (user_id, limit + 1, offset) if user_id else (limit + 1, offset)
        )
        with self.storage.database.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT decision_json FROM risk_events {where}
                ORDER BY t_decision_us DESC LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()
        items = tuple(
            RiskDecision.model_validate_json(row["decision_json"]) for row in rows[:limit]
        )
        return PageResult(items, len(rows) > limit)

    def list_alerts(
        self,
        *,
        offset: int,
        limit: int,
        alert_type: AlertType | None,
    ) -> PageResult[Alert]:
        where = "WHERE alert_type = ?" if alert_type is not None else ""
        parameters: tuple[object, ...] = (
            (alert_type.value, limit + 1, offset) if alert_type is not None else (limit + 1, offset)
        )
        with self.storage.database.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT alert_id, alert_type, severity, code, occurred_at_utc, acknowledged
                FROM alerts {where}
                ORDER BY occurred_at_utc DESC LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()
        items = tuple(
            Alert(
                alert_id=row["alert_id"],
                alert_type=AlertType(row["alert_type"]),
                severity=row["severity"],
                code=row["code"],
                occurred_at=row["occurred_at_utc"],
                acknowledged=bool(row["acknowledged"]),
            )
            for row in rows[:limit]
        )
        return PageResult(items, len(rows) > limit)

    def acknowledge_alert(self, alert_id: str) -> None:
        if not self.storage.acknowledge_alert(alert_id):
            raise ResourceNotFound("alert was not found")

    def list_updates(
        self, *, offset: int, limit: int, user_id: str | None
    ) -> PageResult[UpdateCandidate]:
        where = "WHERE user_id = ?" if user_id is not None else ""
        parameters: tuple[object, ...] = (
            (user_id, limit + 1, offset) if user_id else (limit + 1, offset)
        )
        with self.storage.database.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT candidate_json FROM update_candidates {where}
                ORDER BY quarantined_at_utc DESC LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()
        items = tuple(
            UpdateCandidate.model_validate_json(row["candidate_json"]) for row in rows[:limit]
        )
        return PageResult(items, len(rows) > limit)

    def metrics(self) -> Metrics:
        with self.storage.database.connection() as connection:
            row = connection.execute(
                """
                SELECT metric_json FROM system_metrics
                WHERE metric_kind = 'METRICS' ORDER BY observed_at_utc DESC LIMIT 1
                """
            ).fetchone()
        if row is None:
            return Metrics(
                collector_cpu_percent=None,
                collector_memory_bytes=None,
                dropped_events=0,
                latency_us={},
            )
        return Metrics.model_validate_json(row["metric_json"])

    def health(self) -> Health:
        with self.storage.database.connection() as connection:
            row = connection.execute(
                """
                SELECT metric_json FROM system_metrics
                WHERE metric_kind = 'HEALTH' ORDER BY observed_at_utc DESC LIMIT 1
                """
            ).fetchone()
        if row is None:
            return Health(
                status="UNAVAILABLE",
                components={"collector": "UNAVAILABLE", "storage": "HEALTHY"},
                heartbeat_age_ms=None,
                collection_paused=False,
            )
        return Health.model_validate_json(row["metric_json"])

    def set_shadow_mode(self, enabled: bool) -> None:
        if self.shadow_mode_setter is None:
            raise ResourceConflict("shadow-mode administration is unavailable")
        self.storage.set_shadow_mode(enabled)
        self.shadow_mode_setter(enabled)

    def rollback(self, user_id: str) -> str:
        if self.update_manager is None:
            raise ResourceConflict("model rollback is unavailable")
        return self.update_manager.rollback(user_id).profile_version

    def _challenge(self) -> ChallengeService:
        if self.challenge_service is None:
            raise ResourceConflict("the security challenge is unavailable")
        return self.challenge_service

    def challenge_status(self) -> dict[str, object]:
        """Expose configuration state and the question; never the answer."""

        service = self._challenge()
        return {"configured": service.is_configured(), "question": service.question()}

    def configure_challenge(
        self,
        *,
        question: str,
        answer: str,
        confirm_answer: str,
        current_answer: str | None,
    ) -> None:
        try:
            self._challenge().configure(
                question=question,
                answer=answer,
                confirm_answer=confirm_answer,
                current_answer=current_answer,
            )
        except ChallengeError as exc:
            raise ResourceConflict(str(exc)) from exc

    def respond_to_challenge(
        self,
        *,
        decision_id: str,
        answer: str,
        response_token: str | None,
    ) -> str:
        service = self._challenge()
        try:
            result = service.respond(decision_id, answer, response_token=response_token)
        except UnknownChallenge as exc:
            raise ResourceNotFound(str(exc)) from exc
        except ChallengeError as exc:
            raise ResourceConflict(str(exc)) from exc
        accepted = result.outcome is ResponseOutcome.ACCEPTED
        is_scheduled_anchor = result.challenge.decision_id.startswith("scheduled-anchor:")
        if self.enforcement is not None and not is_scheduled_anchor:
            # A scheduled-anchor decision_id is minted in memory by
            # ChallengeService.open_scheduled and is never inserted into the
            # `decisions` table (it is not a RiskDecision), so routing it
            # through EnforcementCoordinator.record_challenge_response would
            # hit an UPDATE ... WHERE decision_id = ? that matches zero rows
            # and raises StorageUnavailableError -- turning every correctly
            # answered A3 prompt into a 500 with no anchor stored. A
            # scheduled prompt is always SOFT_CHALLENGE and never produces an
            # A2 anchor, so this call has nothing to do for that path anyway;
            # the scheduled_anchor_sink below is what records A3.
            self.enforcement.record_challenge_response(
                decision_id=result.challenge.decision_id,
                requested_action=result.challenge.action,
                user_id=result.challenge.user_id,
                session_id=result.challenge.session_id,
                segment_id=result.challenge.segment_id,
                accepted=accepted,
                code=f"CHALLENGE_{result.outcome.value}",
                evidence_reference=result.evidence_reference,
            )
        if (
            result.outcome is ResponseOutcome.ACCEPTED
            and is_scheduled_anchor
            and self._scheduled_anchor_sink is not None
            and result.evidence_reference is not None
        ):
            # A3 only. An ordinary SOFT_CHALLENGE/REAUTH response already
            # produces its anchor through EnforcementCoordinator; routing it
            # here as well would double-record the evidence.
            self._scheduled_anchor_sink(
                decision_id=result.challenge.decision_id,
                session_id=result.challenge.session_id,
                segment_id=result.challenge.segment_id,
                evidence_reference=result.evidence_reference,
            )
        return result.outcome.value

    def enforcement_status(self) -> dict[str, object]:
        """Observability only: what is outstanding, never the answer."""

        service = self._challenge()
        pending = service.active_challenge()
        return {
            "enforcement_enabled": self.enforcement_settings is not None
            and self.enforcement_settings.enabled,
            "configured": service.is_configured(),
            "pending_challenge": None
            if pending is None
            else {
                "decision_id": pending.decision_id,
                "action": pending.action.value,
                "question": pending.question,
                "blocking": pending.blocking,
                "opened_at": pending.opened_at.isoformat().replace("+00:00", "Z"),
                "expires_at": pending.expires_at.isoformat().replace("+00:00", "Z"),
            },
        }

    def collection_provenance(self) -> dict[str, str]:
        """Report what this run records, derived from the storage profile.

        Read directly from StorageSettings so the reported value cannot
        disagree with what is actually written to disk.
        """

        settings = self.storage.settings
        return {
            "environment": settings.environment.value,
            "data_policy": settings.data_policy.value,
            "collection_provenance": settings.collection_provenance.value,
            "config_version": settings.config_version,
        }
