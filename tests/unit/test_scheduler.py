"""Scheduler: sources run on their own interval and can be rescheduled."""

import asyncio

import pytest
from app.application.orchestration.scheduler import CrawlScheduler
from app.domain.models.source import SourceConfig


def _source(source_id: str, interval_s: float) -> SourceConfig:
    return SourceConfig(
        id=source_id,
        url=f"https://example.com/{source_id}",
        kind="ats",
        schedule={"interval_s": interval_s},
    )


@pytest.mark.asyncio
async def test_scheduler_runs_the_source_runner():
    scheduler = CrawlScheduler()
    calls: list[str] = []

    async def runner() -> None:
        calls.append("ran")

    scheduler.schedule("s1", 0.01, runner)
    await asyncio.sleep(0.05)
    scheduler.cancel_all()

    assert calls, "the scheduler should have run the source at least once"


@pytest.mark.asyncio
async def test_scheduler_skips_sources_without_an_interval():
    scheduler = CrawlScheduler()
    calls: list[str] = []

    async def runner() -> None:
        calls.append("ran")

    scheduler.schedule_sources([_source("s1", 0)], lambda source: runner())
    await asyncio.sleep(0.03)
    scheduler.cancel_all()

    assert calls == []
    assert not scheduler.is_scheduled("s1")


@pytest.mark.asyncio
async def test_schedule_sources_replaces_the_previous_plan():
    scheduler = CrawlScheduler()

    async def runner() -> None:
        return None

    scheduler.schedule_sources(
        [_source("s1", 60), _source("s2", 60)], lambda source: runner()
    )
    assert scheduler.is_scheduled("s1")
    assert scheduler.is_scheduled("s2")

    # s2 is no longer enabled, so it must be dropped from the plan.
    scheduler.schedule_sources([_source("s1", 60)], lambda source: runner())
    assert scheduler.is_scheduled("s1")
    assert not scheduler.is_scheduled("s2")
    scheduler.cancel_all()


@pytest.mark.asyncio
async def test_a_failing_source_does_not_kill_the_loop():
    scheduler = CrawlScheduler()
    calls: list[str] = []

    async def failing() -> None:
        calls.append("boom")
        raise RuntimeError("source is down")

    scheduler.schedule("s1", 0.01, failing)
    await asyncio.sleep(0.05)
    scheduler.cancel_all()

    assert len(calls) > 1, "the loop must keep running after a failure"
