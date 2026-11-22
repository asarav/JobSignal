"""Continuous discovery: bounded passes, tier-aware scheduling, no double crawls."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.application.auto_discover import (
    AUTO_DISCOVER_INTERVAL_S,
    AUTO_DISCOVER_STATE_KEY,
    effective_crawl_interval_s,
)
from app.application.config import AppConfig
from app.application.orchestration.scheduler import CrawlScheduler
from app.domain.models.context import ExecutionContext
from app.domain.models.discovery import DiscoveredSource, DiscoveryQuery
from app.domain.models.source import SourceConfig
from app.worker.main import (
    SCHEDULER_RESYNC_METHODS,
    _auto_discover_interval_s,
    _scheduler_affected,
)
from tests.support.fakes import FakeClock
from tests.unit.test_auto_discover import _Provider, _wire

SOURCE = SourceConfig(
    id="src_acme",
    url="https://jobs.example.com/acme",
    kind="career_site",
    schedule={"interval_s": 43200, "tier": "cold", "tier_reason": "no postings yet"},
)


def test_effective_interval_prefers_tier_unless_funnel_is_disabled() -> None:
    assert effective_crawl_interval_s(SOURCE, {}) == 7 * 24 * 3600
    assert (
        effective_crawl_interval_s(SOURCE, {"tier_intervals_s": [10, 20, 30]}) == 30
    )
    assert effective_crawl_interval_s(SOURCE, {"funnel_enabled": False}) == 43200


def test_discovery_defaults_to_hourly_continuous_passes() -> None:
    assert AUTO_DISCOVER_INTERVAL_S == 3600
    assert _auto_discover_interval_s(AppConfig()) == 3600
    assert (
        _auto_discover_interval_s(
            AppConfig(discovery={"auto_discover_interval_minutes": 5})
        )
        == 300
    )
    # Old settings keep working, and minutes take precedence when both exist.
    assert (
        _auto_discover_interval_s(
            AppConfig(discovery={"auto_discover_interval_hours": 2})
        )
        == 7200
    )
    assert (
        _auto_discover_interval_s(
            AppConfig(
                discovery={
                    "auto_discover_interval_minutes": 10,
                    "auto_discover_interval_hours": 2,
                }
            )
        )
        == 600
    )


class _CaptureScheduler(CrawlScheduler):
    def __init__(self) -> None:
        super().__init__()
        self.intervals: list[tuple[str, float]] = []

    def schedule(
        self, source_id: str, interval_s: float, runner: Any, *, run_immediately: bool = True
    ) -> None:
        self.intervals.append((source_id, interval_s))


@pytest.mark.asyncio
async def test_scheduler_uses_the_tier_interval_resolver() -> None:
    scheduler = _CaptureScheduler()

    async def run() -> None:  # pragma: no cover - never executed by the capture
        return None

    scheduler.schedule_sources(
        [SOURCE], lambda source: run(), interval_for=lambda source: 42.0
    )

    assert scheduler.intervals == [("src_acme", 42.0)]


@pytest.mark.asyncio
async def test_continuous_cadence_waits_for_the_pause_between_passes() -> None:
    clock = FakeClock()
    _, wiring = _wire({}, clock=clock)

    first = await wiring.auto_discover_use_case.run_if_due(900)
    assert first is not None
    assert await wiring.auto_discover_use_case.run_if_due(900) is None

    clock.advance(901)
    assert await wiring.auto_discover_use_case.run_if_due(900) is not None


class _ConcurrentProvider:
    id = "discovery.concurrent"

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls = 0

    async def discover(
        self, query: DiscoveryQuery, ctx: ExecutionContext
    ) -> AsyncIterator[DiscoveredSource]:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
        finally:
            self.active -= 1
        return
        yield  # pragma: no cover


@pytest.mark.asyncio
async def test_overlapping_discovery_passes_are_serialized() -> None:
    provider = _ConcurrentProvider()
    _, wiring = _wire({provider.id: provider})

    await asyncio.gather(
        wiring.auto_discover_use_case.run(),
        wiring.auto_discover_use_case.run(),
    )

    assert provider.calls == 2
    assert provider.max_active == 1, "a second pass must wait for the first"


@pytest.mark.asyncio
async def test_scheduler_skips_a_source_the_pass_just_crawled() -> None:
    clock = FakeClock()
    container, wiring = _wire({}, clock=clock)
    source = await wiring.registry.add(SOURCE.url)

    await wiring.auto_discover_use_case.run()
    skipped = await wiring.run_source(source.id)
    assert skipped.get("skipped") is True

    clock.advance(3700)
    assert await wiring.auto_discover_use_case.is_due(source.id) is True


def test_scheduler_resync_is_needed_after_tier_changes() -> None:
    assert _scheduler_affected(
        {"result": {"registered": 0, "tiers_changed": 1}}
    )
    assert not _scheduler_affected({"result": {"registered": 0, "tiers_changed": 0}})
    assert not _scheduler_affected({"result": None})


def test_only_scheduling_source_methods_resync_the_scheduler() -> None:
    assert "sources.add" in SCHEDULER_RESYNC_METHODS
    assert "sources.set_schedule" in SCHEDULER_RESYNC_METHODS
    # Reading or reordering extraction must not churn every scheduled task.
    assert "sources.list" not in SCHEDULER_RESYNC_METHODS
    assert "sources.set_extraction_order" not in SCHEDULER_RESYNC_METHODS


def test_refreshing_settings_changes_the_pass_interval_without_a_restart() -> None:
    _, wiring = _wire({})
    assert _auto_discover_interval_s(wiring.config) == AUTO_DISCOVER_INTERVAL_S

    wiring.refresh_config(AppConfig(discovery={"auto_discover_interval_minutes": 5}))

    assert _auto_discover_interval_s(wiring.config) == 300


@pytest.mark.asyncio
async def test_index_work_is_capped_per_day_and_does_not_reuse_the_cursor() -> None:
    clock = FakeClock()
    provider = _Provider(["https://jobs.example.com/a"])
    container, wiring = _wire(
        {"discovery.fake": provider},
        clock=clock,
        config=AppConfig(discovery={"max_index_slices_per_day": 12}),
    )

    first = await wiring.auto_discover_use_case.run()
    assert first.discovered == 1
    assert provider.calls == 1

    second = await wiring.auto_discover_use_case.run()
    assert second.discovered == 0
    assert provider.calls == 1, "the day's index budget is spent"
    async with container.uow_factory() as uow:
        state = await uow.settings.get(AUTO_DISCOVER_STATE_KEY)
    assert isinstance(state, dict)
    assert state["rotation_cursor"] == 12, "a pass that queried nothing must not advance"

    # The next day has a fresh budget and the cursor moves again.
    clock.advance(86400)
    third = await wiring.auto_discover_use_case.run()
    assert third.discovered == 1
    assert provider.calls == 2
    async with container.uow_factory() as uow:
        state = await uow.settings.get(AUTO_DISCOVER_STATE_KEY)
    assert isinstance(state, dict)
    assert state["rotation_cursor"] == 24
