"""Bounded C8 stream history, replay cursors, snapshots, and slow-client isolation."""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from protocol.generated.python.contracts import (
    PROTOCOL_VERSION,
    StreamEventType,
    StreamSnapshot,
    WebSocketEnvelope,
    WebSocketPayload,
)


def _emitted_at() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class SlowClient:
    pass


StreamItem = WebSocketEnvelope | SlowClient
SnapshotProvider = Callable[[], StreamSnapshot]


@dataclass(frozen=True)
class StreamSubscription:
    client_id: str
    initial: tuple[WebSocketEnvelope, ...]
    queue: asyncio.Queue[StreamItem]


class EventBroker:
    def __init__(
        self,
        *,
        replay_capacity: int,
        client_capacity: int,
        snapshot_provider: SnapshotProvider,
    ) -> None:
        if replay_capacity < 1 or client_capacity < 1:
            raise ValueError("stream capacities must be positive")
        self._history: deque[WebSocketEnvelope] = deque(maxlen=replay_capacity)
        self._client_capacity = client_capacity
        self._snapshot_provider = snapshot_provider
        self._clients: dict[str, asyncio.Queue[StreamItem]] = {}
        self._sequence = 0
        self._lock = asyncio.Lock()

    @property
    def current_sequence(self) -> int:
        return self._sequence

    def _snapshot(self) -> WebSocketEnvelope:
        snapshot = self._snapshot_provider().model_copy(update={"last_stream_seq": self._sequence})
        return WebSocketEnvelope(
            schema_version=PROTOCOL_VERSION,
            stream_seq=self._sequence,
            event_type=StreamEventType.SNAPSHOT,
            emitted_at=_emitted_at(),
            payload=snapshot,
        )

    async def publish(
        self,
        event_type: StreamEventType,
        payload: WebSocketPayload,
    ) -> WebSocketEnvelope:
        async with self._lock:
            self._sequence += 1
            envelope = WebSocketEnvelope(
                schema_version=PROTOCOL_VERSION,
                stream_seq=self._sequence,
                event_type=event_type,
                emitted_at=_emitted_at(),
                payload=payload,
            )
            self._history.append(envelope)
            slow: list[str] = []
            for client_id, queue in self._clients.items():
                try:
                    queue.put_nowait(envelope)
                except asyncio.QueueFull:
                    slow.append(client_id)
            for client_id in slow:
                queue = self._clients.pop(client_id)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(SlowClient())
            return envelope

    async def subscribe(self, cursor: int | None) -> StreamSubscription:
        if cursor is not None and cursor < 0:
            raise ValueError("stream cursor must be non-negative")
        async with self._lock:
            client_id = f"stream-{uuid.uuid4()}"
            queue: asyncio.Queue[StreamItem] = asyncio.Queue(maxsize=self._client_capacity)
            self._clients[client_id] = queue
            earliest = self._history[0].stream_seq if self._history else self._sequence
            initial: tuple[WebSocketEnvelope, ...]
            if cursor is None or cursor < earliest - 1 or cursor > self._sequence:
                initial = (self._snapshot(),)
            else:
                initial = tuple(item for item in self._history if item.stream_seq > cursor)
            return StreamSubscription(client_id, initial, queue)

    async def unsubscribe(self, client_id: str) -> None:
        async with self._lock:
            self._clients.pop(client_id, None)
