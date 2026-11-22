"""Crawl budgets and the crawl event log (spec §6.6, §17.2, §19.1).

A board with thousands of postings must not turn one crawl into an unbounded
run, and a truncated listing must never be read as "everything else is gone".
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from app.application.config import AppConfig
from app.application.sources import run_crawl
from app.domain.models.common import Err, Ok, PortError
from app.domain.models.context import ExecutionContext
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig
from app.infrastructure.http.policy_client import PolicyHttpClient
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from tests.support.fakes import FakeHttpClient

SOURCE = SourceConfig(
    id="src_budget",
    url="https://boards.example.com/acme",
    kind="ats",
    connector_id="ats.static",
)


class _TickingClock:
    """A clock that jumps ``step_s`` on every read, so a run can age mid-crawl."""

    def __init__(self, step_s: float = 0.0) -> None:
        self._now = datetime(2026, 3, 1, tzinfo=UTC)
        self._step = timedelta(seconds=step_s)

    def now(self) -> datetime:
        current = self._now
        self._now = self._now + self._step
        return current


class _ManyPostingsConnector:
    id = "ats.static"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(list_jobs=True, get_job=True)

    def __init__(self, count: int) -> None:
        self._count = count

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        return MatchResult(matched=True, confidence=1.0, ats_hint="static")

    async def list_jobs(self, source: SourceConfig, ctx: ExecutionContext):
        async def gen() -> AsyncIterator[RawJobRef]:
            for index in range(self._count):
                yield RawJobRef(
                    source_id=source.id,
                    url=f"https://boards.example.com/acme/jobs/{index}",
                    source_posting_id=str(index),
                )

        return gen()

    async def fetch_job(self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext):
        return Ok(
            RawJob(
                ref=ref,
                title=f"Engineer {ref.source_posting_id}",
                body_text=f"Description for posting {ref.source_posting_id}.",
                body_html=None,
                payload={},
            )
        )


class _RefusingConnector(_ManyPostingsConnector):
    """Every posting answers with a robots refusal rather than a page."""

    async def fetch_job(self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext):
        return Err(
            PortError(
                code="robots.disallowed",
                message="robots.txt disallows this path",
                retryable=False,
            )
        )


async def _crawl(connector, factory, clock, *, limits=None):
    source = SOURCE if limits is None else replace(SOURCE, limits=limits)
    return await run_crawl(
        source=source,
        connector=connector,  # type: ignore[arg-type]
        uow_factory=factory,
        http=PolicyHttpClient(transport=FakeHttpClient()),
        clock_now=clock.now,
        config=AppConfig(),
    )


@pytest.mark.asyncio
async def test_the_ref_budget_truncates_a_large_board():
    factory = InMemoryUnitOfWorkFactory()
    clock = _TickingClock()
    stats = await _crawl(
        _ManyPostingsConnector(5), factory, clock, limits={"max_refs": 2}
    )

    assert stats["discovered"] == 2
    assert stats["refs_total"] == 5
    assert stats["truncated"] is True


@pytest.mark.asyncio
async def test_a_truncated_listing_never_marks_postings_removed():
    """The remainder of the board was never looked at, so it cannot be gone."""
    factory = InMemoryUnitOfWorkFactory()
    clock = _TickingClock()
    await _crawl(_ManyPostingsConnector(5), factory, clock, limits={"max_refs": 2})

    statuses = {str(row.get("posting_status")) for row in factory.jobs.values()}
    assert statuses == {"active"}
    run = next(iter(factory.crawl_runs.values()))
    assert run["status"] == "completed"


@pytest.mark.asyncio
async def test_the_duration_budget_stops_a_slow_crawl():
    factory = InMemoryUnitOfWorkFactory()
    clock = _TickingClock(step_s=1.0)
    stats = await _crawl(
        _ManyPostingsConnector(20), factory, clock, limits={"max_duration_s": 3}
    )

    assert stats["truncated"] is True
    assert int(str(stats["discovered"])) < 20


@pytest.mark.asyncio
async def test_a_refused_posting_is_written_to_the_crawl_event_log():
    factory = InMemoryUnitOfWorkFactory()
    clock = _TickingClock()
    stats = await _crawl(_RefusingConnector(1), factory, clock)

    assert stats["errors"] == 1
    assert factory.crawl_events, "a refusal must be queryable, not only logged"
    event = factory.crawl_events[0]
    assert event["code"] == "robots.disallowed"
    assert event["crawl_run_id"]


@pytest.mark.asyncio
async def test_the_config_floor_applies_when_a_source_declares_no_limits():
    from app.application.sources import _source_budget

    config = AppConfig()
    assert _source_budget(SOURCE, config) == (
        config.crawl.max_refs_per_source,
        float(config.crawl.max_duration_s),
    )
    limited = SourceConfig(
        id="s",
        url="https://x.test",
        kind="ats",
        limits={"max_refs": 7, "max_duration_s": 12.5},
    )
    assert _source_budget(limited, config) == (7, 12.5)
