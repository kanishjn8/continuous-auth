"""Async lifecycle that keeps local transport failures independent from API/enforcement."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from backend.app.websocket.broker import EventBroker
from protocol.generated.python.contracts import Alert, AlertType, StreamEventType

from .named_pipe import LocalByteStreamServer, LocalTransportError
from .orchestrator import RuntimeOrchestrator, StreamEmission

AvailabilitySink = Callable[[str], None]
LOGGER = logging.getLogger(__name__)


class IntegratedRuntimeService:
    def __init__(
        self,
        *,
        orchestrator: RuntimeOrchestrator,
        pipe: LocalByteStreamServer,
        broker: EventBroker,
        watchdog_interval_seconds: float,
        availability_sink: AvailabilitySink | None = None,
    ) -> None:
        if watchdog_interval_seconds <= 0:
            raise ValueError("watchdog interval must be positive")
        self.orchestrator = orchestrator
        self.pipe = pipe
        self.broker = broker
        self.watchdog_interval_seconds = watchdog_interval_seconds
        self.availability_sink = availability_sink
        self._stop = asyncio.Event()
        self._active_failures: set[str] = set()

    async def _publish(self, emissions: tuple[StreamEmission, ...]) -> None:
        for emission in emissions:
            await self.broker.publish(emission.event_type, emission.payload)

    async def publish(self, emissions: tuple[StreamEmission, ...]) -> None:
        await self._publish(emissions)

    async def _signal_availability(self, code: str) -> None:
        """Emit a content-free recovery signal even when persistence is unavailable."""

        if code in self._active_failures:
            return
        self._active_failures.add(code)
        LOGGER.error("%s", code)
        if self.availability_sink is not None:
            try:
                self.availability_sink(code)
            except Exception:
                LOGGER.error("RUNTIME_AVAILABILITY_SINK_FAILED")
        alert = Alert(
            alert_id=f"alert-{uuid.uuid4()}",
            alert_type=AlertType.AVAILABILITY,
            severity="HIGH",
            code=code,
            occurred_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            acknowledged=False,
        )
        try:
            self.orchestrator.storage.store_alert(alert)
        except Exception:
            # The WebSocket path remains useful when the failure is in storage.
            LOGGER.error("RUNTIME_AVAILABILITY_AUDIT_FAILED")
        await self.broker.publish(StreamEventType.ALERT, alert)

    async def _recover_transport(self) -> None:
        try:
            _, emissions = self.orchestrator.transport_disconnected()
            await self._publish(emissions)
        except Exception:
            await self._signal_availability("TRANSPORT_RECOVERY_FAILED")

    async def _pipe_loop(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = await asyncio.to_thread(self.pipe.read)
                if chunk:
                    _, emissions = self.orchestrator.feed(chunk)
                else:
                    _, emissions = self.orchestrator.transport_disconnected()
                await self._publish(emissions)
                self._active_failures.difference_update(
                    {
                        "COLLECTOR_PIPE_UNAVAILABLE",
                        "RUNTIME_PIPELINE_FAILED",
                        "TRANSPORT_RECOVERY_FAILED",
                    }
                )
            except LocalTransportError:
                await self._recover_transport()
                await self._signal_availability("COLLECTOR_PIPE_UNAVAILABLE")
                await asyncio.sleep(self.watchdog_interval_seconds)
            except Exception:
                await self._recover_transport()
                await self._signal_availability("RUNTIME_PIPELINE_FAILED")
                await asyncio.sleep(self.watchdog_interval_seconds)

    async def _watchdog_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.watchdog_interval_seconds)
            try:
                await self._publish(self.orchestrator.check_heartbeat())
                self._active_failures.discard("HEARTBEAT_WATCHDOG_FAILED")
            except Exception:
                await self._signal_availability("HEARTBEAT_WATCHDOG_FAILED")

    async def run(self) -> None:
        pipe_task = asyncio.create_task(self._pipe_loop(), name="collector-pipe")
        watchdog_task = asyncio.create_task(self._watchdog_loop(), name="heartbeat-watchdog")
        try:
            await self._stop.wait()
        finally:
            self.pipe.close()
            pipe_task.cancel()
            watchdog_task.cancel()
            await asyncio.gather(pipe_task, watchdog_task, return_exceptions=True)
            await self._publish(self.orchestrator.shutdown())

    def stop(self) -> None:
        self._stop.set()
