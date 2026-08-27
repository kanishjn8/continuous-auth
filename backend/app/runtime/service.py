"""Async lifecycle that keeps pipe failures independent from API/enforcement."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from backend.app.websocket.broker import EventBroker

from .named_pipe import NamedPipeError, WindowsNamedPipeServer
from .orchestrator import RuntimeOrchestrator, StreamEmission

AvailabilitySink = Callable[[str], None]


class IntegratedRuntimeService:
    def __init__(
        self,
        *,
        orchestrator: RuntimeOrchestrator,
        pipe: WindowsNamedPipeServer,
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

    async def _publish(self, emissions: tuple[StreamEmission, ...]) -> None:
        for emission in emissions:
            await self.broker.publish(emission.event_type, emission.payload)

    async def publish(self, emissions: tuple[StreamEmission, ...]) -> None:
        await self._publish(emissions)

    async def _pipe_loop(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = await asyncio.to_thread(self.pipe.read)
                if chunk:
                    _, emissions = self.orchestrator.feed(chunk)
                else:
                    _, emissions = self.orchestrator.transport_disconnected()
                await self._publish(emissions)
            except NamedPipeError as exc:
                if self.availability_sink is not None:
                    self.availability_sink(str(exc))
                await asyncio.sleep(self.watchdog_interval_seconds)

    async def _watchdog_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.watchdog_interval_seconds)
            await self._publish(self.orchestrator.check_heartbeat())

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
