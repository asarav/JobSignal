"""In-process crawl scheduler (M1).

A deliberately simple scheduler: each source runs on its own interval in a
background task. Production-grade scheduling (cron, jitter, persistence) lands
in M8; this keeps M1 testable without a daemon.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.domain.models.source import SourceConfig


class CrawlScheduler:
    """Runs a callable repeatedly, once per source, on its own interval."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def schedule(
        self,
        source_id: str,
        interval_s: float,
        runner: Callable[[], Awaitable[Any]],
        *,
        run_immediately: bool = True,
    ) -> None:
        if source_id in self._tasks:
            self._tasks[source_id].cancel()
        self._tasks[source_id] = asyncio.create_task(
            self._loop(source_id, interval_s, runner, run_immediately=run_immediately)
        )

    def schedule_sources(
        self,
        sources: list[SourceConfig],
        runner_for: Callable[[SourceConfig], Awaitable[Any]],
        *,
        run_immediately: bool = True,
    ) -> None:
        """(Re)schedule every source that declares a positive interval.

        ``run_immediately=False`` waits one interval first, which avoids
        re-crawling sources that a discovery pass just crawled.
        """
        wanted = {source.id: _interval_of(source) for source in sources}
        for source_id in list(self._tasks):
            if source_id not in wanted:
                self.cancel(source_id)
        for source in sources:
            interval = wanted[source.id]
            if interval <= 0:
                continue

            async def run(source: SourceConfig = source) -> Any:
                return await runner_for(source)

            self.schedule(
                source.id, interval, run, run_immediately=run_immediately
            )

    async def _loop(
        self,
        source_id: str,
        interval_s: float,
        runner: Callable[[], Awaitable[Any]],
        *,
        run_immediately: bool = True,
    ) -> None:
        if not run_immediately:
            await asyncio.sleep(interval_s)
        while True:
            try:
                await runner()
            except Exception:  # noqa: BLE001 - a failing source must not kill the loop
                pass
            await asyncio.sleep(interval_s)

    def cancel(self, source_id: str) -> None:
        task = self._tasks.pop(source_id, None)
        if task is not None:
            task.cancel()

    def cancel_all(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        self._tasks.clear()

    def is_scheduled(self, source_id: str) -> bool:
        return source_id in self._tasks


def _interval_of(source: SourceConfig) -> float:
    """Read the crawl interval (seconds) from a source's schedule config."""
    raw = source.schedule.get("interval_s", 0)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0.0
    return float(raw)
