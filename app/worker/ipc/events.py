"""Worker-side event publisher that feeds the NDJSON writer queue.

``EventPublisher`` is synchronous by design (plugins publish without awaiting),
so publishing only enqueues. ``event_writer`` drains the queue and serializes
writes with the response path.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.domain.ports.clock import Clock
from app.worker.ipc.envelope import event_envelope


class QueuedEventPublisher:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock
        self._seq = 0
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def publish(
        self,
        topic: str,
        payload: dict[str, Any] | Any,
        *,
        task_id: str | None = None,
    ) -> None:
        self._seq += 1
        now = self._clock.now() if self._clock is not None else datetime.now(UTC)
        data = dict(payload) if isinstance(payload, dict) else {"value": payload}
        envelope = event_envelope(
            topic,
            data,
            seq=self._seq,
            ts=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            task_id=task_id,
        )
        self.queue.put_nowait(envelope.to_dict())


async def event_writer(queue: asyncio.Queue[dict[str, Any]], write) -> None:
    while True:
        event = await queue.get()
        try:
            await write(event)
        finally:
            queue.task_done()
