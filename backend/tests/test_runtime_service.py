from __future__ import annotations

import asyncio
from typing import Any, cast

from backend.app.runtime.named_pipe import NamedPipeError
from backend.app.runtime.service import IntegratedRuntimeService
from backend.app.websocket import EventBroker
from protocol.generated.python.contracts import (
    CurrentState,
    Health,
    RiskLevel,
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


class _Storage:
    def __init__(self) -> None:
        self.alert_codes: list[str] = []

    def store_alert(self, alert: Any) -> None:
        self.alert_codes.append(str(alert.code))


class _Orchestrator:
    def __init__(self) -> None:
        self.storage = _Storage()
        self.recoveries = 0
        self.feeds = 0

    def transport_disconnected(self) -> tuple[None, tuple[()]]:
        self.recoveries += 1
        return None, ()

    def feed(self, chunk: bytes) -> tuple[None, tuple[()]]:
        assert chunk == b"synthetic-frame"
        self.feeds += 1
        return None, ()

    def check_heartbeat(self) -> tuple[()]:
        return ()

    def shutdown(self) -> tuple[()]:
        return ()


class _Pipe:
    def __init__(self) -> None:
        self.reads = 0
        self.closed = False

    def read(self) -> bytes:
        self.reads += 1
        if self.reads == 1:
            raise NamedPipeError("detail that must not be signalled")
        return b"synthetic-frame"

    def close(self) -> None:
        self.closed = True


def test_pipe_failure_is_content_free_recoverable_and_deduplicated() -> None:
    async def scenario() -> None:
        orchestrator = _Orchestrator()
        pipe = _Pipe()
        signalled: list[str] = []
        broker = EventBroker(
            replay_capacity=4,
            client_capacity=2,
            snapshot_provider=_snapshot,
        )
        service = IntegratedRuntimeService(
            orchestrator=cast(Any, orchestrator),
            pipe=cast(Any, pipe),
            broker=broker,
            watchdog_interval_seconds=0.001,
            availability_sink=signalled.append,
        )
        task = asyncio.create_task(service.run())
        while orchestrator.feeds == 0:
            await asyncio.sleep(0.001)
        service.stop()
        await asyncio.wait_for(task, timeout=1)

        assert orchestrator.recoveries == 1
        assert signalled == ["COLLECTOR_PIPE_UNAVAILABLE"]
        assert orchestrator.storage.alert_codes == ["COLLECTOR_PIPE_UNAVAILABLE"]
        assert pipe.closed

    asyncio.run(scenario())
