"""Authenticated C7 routes with safe errors, correlation IDs, and bounded pagination."""

# FastAPI dependency injection intentionally evaluates Depends objects in signatures.
# ruff: noqa: B008

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.middleware.base import RequestResponseEndpoint

from backend.app.storage.errors import StorageError
from backend.app.websocket.broker import EventBroker, SlowClient
from protocol.generated.python.contracts import (
    AdminActionResponse,
    AlertPage,
    AlertType,
    ApiError,
    AuthLoginRequest,
    AuthSession,
    CurrentState,
    DecisionPage,
    Health,
    Metrics,
    PageMetadata,
    Profile,
    ProfileCreate,
    ProfilePage,
    StreamSnapshot,
    UpdateCandidatePage,
)

from .auth import LocalSessionAuth, SessionIdentity
from .backend import ApiBackendError, ResourceConflict, ResourceNotFound, SQLiteApiBackend
from .config import ApiSettings
from .cursors import CursorCodec, InvalidCursor

COOKIE_NAME = "ca_session"


@dataclass(frozen=True)
class ApiContext:
    settings: ApiSettings
    backend: SQLiteApiBackend
    auth: LocalSessionAuth
    cursors: CursorCodec
    broker: EventBroker


class _ShadowModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool


class _ChallengeSetupRequest(BaseModel):
    """First-run setup, or a rotation that re-states the current answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str
    answer: str
    confirm_answer: str
    current_answer: str | None = None


class _ChallengeResponseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str


def _correlation(request: Request) -> str:
    return str(request.state.correlation_id)


def _safe_error(request: Request, status: int, code: str, message: str) -> JSONResponse:
    body = ApiError(code=code, message=message, correlation_id=_correlation(request))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def _next_page(
    context: ApiContext,
    *,
    resource: str,
    scope: str,
    offset: int,
    limit: int,
    has_more: bool,
) -> PageMetadata:
    return PageMetadata(
        next_cursor=(
            context.cursors.encode(resource=resource, offset=offset + limit, scope=scope)
            if has_more
            else None
        ),
        has_more=has_more,
    )


def _page_limit(context: ApiContext, requested: int | None) -> int:
    resolved = context.settings.api.default_page_size if requested is None else requested
    if resolved > context.settings.api.max_page_size:
        raise InvalidCursor("requested page is too large")
    return resolved


def create_api_app(
    *,
    settings: ApiSettings,
    backend: SQLiteApiBackend,
    local_secret: str,
) -> FastAPI:
    auth = LocalSessionAuth(
        local_secret,
        ttl=timedelta(seconds=settings.api.session_ttl_seconds),
        hash_iterations=settings.api.secret_hash_iterations,
        max_sessions=settings.api.max_sessions,
    )
    cursors = CursorCodec(secrets.token_bytes(32))

    def snapshot() -> StreamSnapshot:
        alerts = backend.list_alerts(
            offset=0,
            limit=settings.api.snapshot_alert_limit,
            alert_type=None,
        )
        return StreamSnapshot(
            current_state=backend.current_state(),
            recent_alerts=list(alerts.items),
            health=backend.health(),
            last_stream_seq=0,
        )

    broker = EventBroker(
        replay_capacity=settings.api.replay_event_capacity,
        client_capacity=settings.api.websocket_client_capacity,
        snapshot_provider=snapshot,
    )
    context = ApiContext(settings, backend, auth, cursors, broker)
    app = FastAPI(
        title="Continuous Authentication Backend",
        version=settings.protocol_version,
        docs_url=None,
        redoc_url=None,
    )
    app.state.api_context = context

    @app.middleware("http")
    async def correlation_middleware(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        supplied = request.headers.get("X-Correlation-ID")
        request.state.correlation_id = (
            supplied if supplied and len(supplied) <= 128 else secrets.token_hex(16)
        )
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        del exc
        return _safe_error(request, 422, "INVALID_REQUEST", "request validation failed")

    @app.exception_handler(InvalidCursor)
    async def invalid_cursor(request: Request, exc: InvalidCursor) -> JSONResponse:
        del exc
        return _safe_error(request, 400, "INVALID_CURSOR", "pagination cursor is invalid")

    @app.exception_handler(ResourceNotFound)
    async def not_found(request: Request, exc: ResourceNotFound) -> JSONResponse:
        return _safe_error(request, 404, exc.code, str(exc))

    @app.exception_handler(ResourceConflict)
    async def conflict(request: Request, exc: ResourceConflict) -> JSONResponse:
        return _safe_error(request, 409, exc.code, str(exc))

    @app.exception_handler(ApiBackendError)
    async def backend_error(request: Request, exc: ApiBackendError) -> JSONResponse:
        del exc
        return _safe_error(request, 500, "BACKEND_ERROR", "backend operation failed")

    @app.exception_handler(StorageError)
    async def storage_error(request: Request, exc: StorageError) -> JSONResponse:
        del exc
        return _safe_error(request, 503, "STORAGE_UNAVAILABLE", "local storage is unavailable")

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        del exc
        return _safe_error(request, 500, "INTERNAL_ERROR", "internal operation failed")

    def require_session(ca_session: str | None = Cookie(default=None)) -> SessionIdentity:
        identity = auth.authenticate(ca_session)
        if identity is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return identity

    def require_unenforced(
        identity: SessionIdentity = Depends(require_session),
    ) -> SessionIdentity:
        """Serve a protected resource only while enforcement is not blocking.

        This is the backend enforcement point PLAN.md Section 5.2 requires
        when it says the dashboard must not be the only one. It runs after
        authentication, on every protected route, so a page refresh, a new
        tab, a fresh login, or a direct request cannot walk past an
        outstanding reauthentication or a lockout: the posture lives with the
        runtime session, not with the browser's cookie.

        The sweep runs first so that a challenge which expired while nobody
        was looking has already escalated by the time the gate reads the
        posture -- otherwise the first request after an ignored
        reauthentication would be served.

        A soft challenge does not block; see ``EnforcementSessionState``.
        """

        backend.sweep_expired_challenges()
        state = backend.session_state
        if state is not None and state.blocks_protected_access():
            raise HTTPException(status_code=403, detail=state.posture.value)
        return identity

    @app.exception_handler(401)
    async def unauthorized(request: Request, exc: object) -> JSONResponse:
        del exc
        return _safe_error(request, 401, "AUTHENTICATION_REQUIRED", "authentication required")

    @app.exception_handler(403)
    async def enforcement_blocked(request: Request, exc: object) -> JSONResponse:
        posture = getattr(exc, "detail", "REAUTH_REQUIRED")
        code = "SESSION_LOCKED_OUT" if posture == "LOCKED_OUT" else "REAUTHENTICATION_REQUIRED"
        return _safe_error(
            request,
            403,
            code,
            "identity verification is required before this session may continue",
        )

    @app.post("/v1/auth/login", response_model=AuthSession)
    def login(body: AuthLoginRequest, request: Request, response: Response) -> AuthSession:
        created = auth.create(body.local_secret)
        if created is None:
            raise HTTPException(status_code=401, detail="authentication failed")
        token, expires = created
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=settings.api.session_ttl_seconds,
            httponly=True,
            samesite="strict",
            secure=False,
        )
        return AuthSession(
            authenticated=True,
            expires_at=expires.isoformat().replace("+00:00", "Z"),
            correlation_id=_correlation(request),
        )

    @app.get("/v1/auth/session", response_model=AuthSession)
    def session(
        request: Request, identity: SessionIdentity = Depends(require_session)
    ) -> AuthSession:
        return AuthSession(
            authenticated=True,
            expires_at=identity.expires_at.isoformat().replace("+00:00", "Z"),
            correlation_id=_correlation(request),
        )

    @app.post("/v1/auth/logout", response_model=AuthSession)
    def logout(
        request: Request,
        response: Response,
        ca_session: str | None = Cookie(default=None),
        identity: SessionIdentity = Depends(require_session),
    ) -> AuthSession:
        del identity
        auth.revoke(ca_session)
        response.delete_cookie(COOKIE_NAME)
        return AuthSession(
            authenticated=False, expires_at=None, correlation_id=_correlation(request)
        )

    @app.get("/v1/state", response_model=CurrentState)
    def state(identity: SessionIdentity = Depends(require_unenforced)) -> CurrentState:
        del identity
        return backend.current_state()

    @app.get("/v1/profiles", response_model=ProfilePage)
    def profiles(
        cursor: str | None = None,
        limit: int | None = Query(default=None, ge=1),
        identity: SessionIdentity = Depends(require_unenforced),
    ) -> ProfilePage:
        del identity
        resolved_limit = _page_limit(context, limit)
        offset = cursors.decode(cursor, resource="profiles")
        result = backend.list_profiles(offset=offset, limit=resolved_limit)
        return ProfilePage(
            items=list(result.items),
            page=_next_page(
                context,
                resource="profiles",
                scope="",
                offset=offset,
                limit=resolved_limit,
                has_more=result.has_more,
            ),
        )

    @app.post("/v1/profiles", response_model=Profile, status_code=201)
    def create_profile(
        body: ProfileCreate,
        identity: SessionIdentity = Depends(require_unenforced),
    ) -> Profile:
        del identity
        return backend.create_profile(body.user_id)

    @app.get("/v1/profiles/{user_id}", response_model=Profile)
    def profile(user_id: str, identity: SessionIdentity = Depends(require_unenforced)) -> Profile:
        del identity
        return backend.get_profile(user_id)

    @app.get("/v1/history/decisions", response_model=DecisionPage)
    def decisions(
        cursor: str | None = None,
        limit: int | None = Query(default=None, ge=1),
        user_id: str | None = None,
        identity: SessionIdentity = Depends(require_unenforced),
    ) -> DecisionPage:
        del identity
        resolved_limit = _page_limit(context, limit)
        scope = user_id or ""
        offset = cursors.decode(cursor, resource="decisions", scope=scope)
        result = backend.list_decisions(offset=offset, limit=resolved_limit, user_id=user_id)
        return DecisionPage(
            items=list(result.items),
            page=_next_page(
                context,
                resource="decisions",
                scope=scope,
                offset=offset,
                limit=resolved_limit,
                has_more=result.has_more,
            ),
        )

    @app.get("/v1/alerts", response_model=AlertPage)
    def alerts(
        cursor: str | None = None,
        limit: int | None = Query(default=None, ge=1),
        alert_type: AlertType | None = None,
        identity: SessionIdentity = Depends(require_unenforced),
    ) -> AlertPage:
        del identity
        resolved_limit = _page_limit(context, limit)
        scope = alert_type.value if alert_type else ""
        offset = cursors.decode(cursor, resource="alerts", scope=scope)
        result = backend.list_alerts(offset=offset, limit=resolved_limit, alert_type=alert_type)
        return AlertPage(
            items=list(result.items),
            page=_next_page(
                context,
                resource="alerts",
                scope=scope,
                offset=offset,
                limit=resolved_limit,
                has_more=result.has_more,
            ),
        )

    @app.post("/v1/alerts/{alert_id}/acknowledge", response_model=AdminActionResponse)
    def acknowledge(
        alert_id: str,
        request: Request,
        identity: SessionIdentity = Depends(require_unenforced),
    ) -> AdminActionResponse:
        del identity
        backend.acknowledge_alert(alert_id)
        return AdminActionResponse(
            accepted=True, code="ALERT_ACKNOWLEDGED", correlation_id=_correlation(request)
        )

    @app.get("/v1/updates", response_model=UpdateCandidatePage)
    def updates(
        cursor: str | None = None,
        limit: int | None = Query(default=None, ge=1),
        user_id: str | None = None,
        identity: SessionIdentity = Depends(require_unenforced),
    ) -> UpdateCandidatePage:
        del identity
        resolved_limit = _page_limit(context, limit)
        scope = user_id or ""
        offset = cursors.decode(cursor, resource="updates", scope=scope)
        result = backend.list_updates(offset=offset, limit=resolved_limit, user_id=user_id)
        return UpdateCandidatePage(
            items=list(result.items),
            page=_next_page(
                context,
                resource="updates",
                scope=scope,
                offset=offset,
                limit=resolved_limit,
                has_more=result.has_more,
            ),
        )

    @app.get("/v1/metrics", response_model=Metrics)
    def metrics(identity: SessionIdentity = Depends(require_unenforced)) -> Metrics:
        del identity
        return backend.metrics()

    @app.get("/v1/health", response_model=Health)
    def health(identity: SessionIdentity = Depends(require_unenforced)) -> Health:
        del identity
        return backend.health()

    @app.put("/v1/admin/shadow-mode")
    def shadow_mode(
        body: _ShadowModeRequest,
        request: Request,
        identity: SessionIdentity = Depends(require_unenforced),
    ) -> dict[str, object]:
        del identity
        backend.set_shadow_mode(body.enabled)
        return {"enabled": body.enabled, "correlation_id": _correlation(request)}

    @app.post("/v1/admin/models/{user_id}/rollback", response_model=AdminActionResponse)
    def rollback(
        user_id: str,
        request: Request,
        identity: SessionIdentity = Depends(require_unenforced),
    ) -> AdminActionResponse:
        del identity
        backend.rollback(user_id)
        return AdminActionResponse(
            accepted=True, code="MODEL_PROFILE_ROLLED_BACK", correlation_id=_correlation(request)
        )

    @app.get("/v1/enforcement/challenge")
    def challenge_status(
        identity: SessionIdentity = Depends(require_session),
    ) -> dict[str, object]:
        """Report whether first-run setup is complete, and the question only.

        The answer never leaves the process in any form.
        """

        del identity
        return backend.challenge_status()

    @app.put("/v1/enforcement/challenge")
    def configure_challenge(
        body: _ChallengeSetupRequest,
        request: Request,
        identity: SessionIdentity = Depends(require_unenforced),
    ) -> dict[str, object]:
        """Set or rotate the challenge; blocked while enforcement is blocking.

        Rotation already demands the current answer, but the gate closes the
        remaining hole: the dashboard is co-located with the monitored
        endpoint (PLAN.md 14.4), so someone occupying a session that has been
        escalated must not be able to touch the credential that ends the
        escalation. First-run setup is therefore only possible before any
        enforcement has fired -- which is what the pre-drill checklist
        requires anyway.
        """

        del identity
        backend.configure_challenge(
            question=body.question,
            answer=body.answer,
            confirm_answer=body.confirm_answer,
            current_answer=body.current_answer,
        )
        return {"configured": True, "correlation_id": _correlation(request)}

    @app.post("/v1/enforcement/challenge/{decision_id}/respond")
    def respond_to_challenge(
        decision_id: str,
        body: _ChallengeResponseRequest,
        request: Request,
        ca_session: str | None = Cookie(default=None),
        x_challenge_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        """Resolve a dispatched challenge.

        Two callers are legitimate: the native prompt, which holds the
        one-time token for this decision, and an authenticated dashboard
        session. The prompt has no session cookie, so the token is the only
        credential it can present.
        """

        if x_challenge_token is None and auth.authenticate(ca_session) is None:
            raise HTTPException(status_code=401, detail="authentication required")
        outcome = backend.respond_to_challenge(
            decision_id=decision_id,
            answer=body.answer,
            response_token=x_challenge_token,
        )
        return {"outcome": outcome, "correlation_id": _correlation(request)}

    @app.post("/v1/enforcement/reauthenticate")
    def request_reauthentication(
        request: Request,
        identity: SessionIdentity = Depends(require_session),
    ) -> dict[str, object]:
        """Issue the challenge that clears a blocking enforcement posture.

        Deliberately not behind ``require_unenforced``: it is the one thing a
        blocked session is supposed to be able to do. It returns the question
        and a decision id only -- the answer is then submitted through the
        existing response endpoint, so verification, audit, and the A2 anchor
        all run through exactly one code path.

        It refuses when nothing is blocking, so a quiet session cannot mint
        reauthentication evidence on demand.
        """

        del identity
        opened = backend.open_reauthentication()
        return {**opened, "correlation_id": _correlation(request)}

    @app.get("/v1/enforcement/status")
    def enforcement_status(
        identity: SessionIdentity = Depends(require_session),
    ) -> dict[str, object]:
        del identity
        return backend.enforcement_status()

    @app.get("/v1/collection/provenance")
    def collection_provenance(
        identity: SessionIdentity = Depends(require_unenforced),
    ) -> dict[str, str]:
        del identity
        return backend.collection_provenance()

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "alive", "protocol_version": settings.protocol_version}

    from fastapi import WebSocket, WebSocketDisconnect

    # Registered via app.router.add_websocket_route (below) rather than the
    # @app.websocket decorator: with `from __future__ import annotations` active
    # in this module, FastAPI's websocket dependant-builder fails to recognize
    # the `websocket: WebSocket` parameter as the special connection object and
    # instead treats it as a required query field, rejecting every connection
    # before it reaches this function. Starlette's plain route registration
    # calls this coroutine directly with no dependant/query-injection step, so
    # `cursor` is parsed from the query string manually below.
    async def stream(websocket: WebSocket) -> None:
        cursor_param = websocket.query_params.get("cursor")
        cursor = int(cursor_param) if cursor_param is not None else None
        token = websocket.cookies.get(COOKIE_NAME)
        if auth.authenticate(token) is None:
            await websocket.accept()
            await websocket.close(code=4401)
            return
        # The stream carries risk decisions and alerts, which are protected
        # resources like any other. Reconnecting must not become the route
        # around an outstanding reauthentication -- the dashboard reconnects
        # automatically, so without this the gate would last exactly one
        # request. 4403 is distinct from the 4401 the client already treats
        # as "log in again".
        backend.sweep_expired_challenges()
        state = backend.session_state
        if state is not None and state.blocks_protected_access():
            await websocket.accept()
            await websocket.close(code=4403)
            return
        subscription = await broker.subscribe(cursor)
        await websocket.accept()
        try:
            for envelope in subscription.initial:
                await websocket.send_json(envelope.model_dump(mode="json"))
            while True:
                item = await subscription.queue.get()
                if isinstance(item, SlowClient):
                    await websocket.close(code=4408)
                    return
                await websocket.send_json(item.model_dump(mode="json"))
        except WebSocketDisconnect:
            return
        finally:
            await broker.unsubscribe(subscription.client_id)

    app.router.add_websocket_route("/v1/stream", stream, name="stream")

    return app
