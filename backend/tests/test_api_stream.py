from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from backend.app.api.auth import LocalSessionAuth
from backend.app.api.cursors import CursorCodec, InvalidCursor
from backend.app.websocket import EventBroker, SlowClient
from protocol.generated.python.contracts import (
    CurrentState,
    Health,
    RiskLevel,
    StreamEventType,
    StreamSnapshot,
    UserState,
)


def _snapshot() -> StreamSnapshot:
    return StreamSnapshot(
        current_state=CurrentState(
            schema_version="1.0.0",
            user_id=None,
            user_state=UserState.SUSPENDED,
            risk_level=RiskLevel.UNAVAILABLE,
            protection_available=False,
            last_check_at="2026-01-01T00:00:00Z",
        ),
        recent_alerts=[],
        health=Health(
            status="UNAVAILABLE",
            components={"collector": "UNAVAILABLE"},
            heartbeat_age_ms=None,
            collection_paused=False,
        ),
        last_stream_seq=0,
    )


def test_local_auth_and_cursor_tamper_rejection() -> None:
    auth = LocalSessionAuth(
        "synthetic-secret",
        ttl=timedelta(seconds=60),
        hash_iterations=100_000,
        max_sessions=2,
    )
    assert auth.create("wrong") is None
    created = auth.create("synthetic-secret")
    assert created is not None
    token, _ = created
    assert auth.authenticate(token) is not None
    auth.revoke(token)
    assert auth.authenticate(token) is None

    cursors = CursorCodec(b"synthetic-signing-key-material-32")
    cursor = cursors.encode(resource="profiles", offset=5, scope="")
    assert cursors.decode(cursor, resource="profiles") == 5
    with pytest.raises(InvalidCursor):
        cursors.decode(cursor + "x", resource="profiles")
    with pytest.raises(InvalidCursor):
        cursors.decode(cursor, resource="alerts")


def test_stream_snapshot_replay_and_slow_client_isolation() -> None:
    async def scenario() -> None:
        broker = EventBroker(replay_capacity=2, client_capacity=1, snapshot_provider=_snapshot)
        initial = await broker.subscribe(None)
        assert initial.initial[0].event_type is StreamEventType.SNAPSHOT
        health = _snapshot().health
        first = await broker.publish(StreamEventType.HEALTH, health)
        await broker.publish(StreamEventType.HEALTH, health)
        slow = await initial.queue.get()
        assert isinstance(slow, SlowClient)
        replay = await broker.subscribe(first.stream_seq)
        assert len(replay.initial) == 1
        assert replay.initial[0].stream_seq == first.stream_seq + 1
        await broker.publish(StreamEventType.HEALTH, health)
        stale = await broker.subscribe(0)
        assert stale.initial[0].event_type is StreamEventType.SNAPSHOT

    asyncio.run(scenario())
