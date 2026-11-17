"""Integration: crawl runner creates jobs, skips unchanged ones, isolates failures."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from app.application.sources import run_crawl
from app.domain.models.common import Ok
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpResponse
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig
from app.infrastructure.http.policy_client import PolicyHttpClient
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from app.plugins.ats.greenhouse import GreenhouseConnector
from tests.support.fakes import FakeClock, FakeHttpClient


def _board_http() -> FakeHttpClient:
    payload = {
        "jobs": [
            {"id": 1, "title": "Backend Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1"},
            {"id": 2, "title": "Frontend Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/2"},
        ]
    }
    return FakeHttpClient(
        {
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs": HttpResponse(
                status=200,
                url="https://boards-api.greenhouse.io/v1/boards/acme/jobs",
                headers={"content-type": "application/json"},
                body=json.dumps(payload).encode(),
                elapsed_ms=5,
            )
        }
    )


class _StaticConnector:
    """A connector that always returns one posting, for deterministic tests."""

    id = "ats.static"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(list_jobs=True, get_job=True)

    def __init__(self, title: str, description: str, url: str = "https://example.com/jobs/1") -> None:
        self._title = title
        self._description = description
        self._url = url

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        return MatchResult(matched=True, confidence=1.0, ats_hint="static")

    async def list_jobs(self, source: SourceConfig, ctx: ExecutionContext) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            yield RawJobRef(source_id=source.id, url=self._url, source_posting_id="42")

        return gen()

    async def fetch_job(self, ref: RawJobRef, ctx: ExecutionContext):

        return Ok(
            RawJob(
                ref=ref,
                title=self._title,
                body_text=self._description,
                body_html=None,
                payload={},
            )
        )


@pytest.mark.asyncio
async def test_crawl_creates_jobs_and_marks_not_seen():
    factory = InMemoryUnitOfWorkFactory()
    http = _board_http()
    policy = PolicyHttpClient(transport=http)
    clock = FakeClock()
    source = SourceConfig(
        id="s1", url="https://boards.greenhouse.io/acme", kind="ats",
        connector_id="ats.greenhouse", ats_tenant_key="acme",
    )
    async with factory() as uow:
        await uow.sources.add(source)
        await uow.commit()

    connector = GreenhouseConnector()
    stats = await run_crawl(
        source=source, connector=connector, uow_factory=factory,
        http=policy, clock_now=clock.now,
    )
    assert stats["discovered"] == 2
    assert stats["new"] == 2

    async with factory() as uow:
        jobs = await uow.jobs.list()
        assert len(jobs) == 2
        runs = await uow.crawl_runs.list()
        assert runs[0]["status"] == "completed"
        assert runs[0]["stats"].get("discovered") == 2


@pytest.mark.asyncio
async def test_crawl_skips_unchanged_jobs_on_second_run():
    factory = InMemoryUnitOfWorkFactory()
    http = _board_http()
    policy = PolicyHttpClient(transport=http)
    clock = FakeClock()
    source = SourceConfig(
        id="s1", url="https://boards.greenhouse.io/acme", kind="ats",
        connector_id="ats.greenhouse", ats_tenant_key="acme",
    )
    async with factory() as uow:
        await uow.sources.add(source)
        await uow.commit()

    connector = GreenhouseConnector()
    await run_crawl(source=source, connector=connector, uow_factory=factory, http=policy, clock_now=clock.now)
    stats2 = await run_crawl(source=source, connector=connector, uow_factory=factory, http=policy, clock_now=clock.now)
    assert stats2["discovered"] == 2
    assert stats2["new"] == 0
    assert stats2["updated"] == 0
    assert stats2["skipped"] == 2


@pytest.mark.asyncio
async def test_missing_job_is_marked_removed():
    factory = InMemoryUnitOfWorkFactory()
    http = _board_http()
    policy = PolicyHttpClient(transport=http)
    clock = FakeClock()
    source = SourceConfig(
        id="s1", url="https://boards.greenhouse.io/acme", kind="ats",
        connector_id="ats.greenhouse", ats_tenant_key="acme",
    )
    async with factory() as uow:
        await uow.sources.add(source)
        await uow.commit()

    connector = GreenhouseConnector()
    await run_crawl(source=source, connector=connector, uow_factory=factory, http=policy, clock_now=clock.now)

    empty_http = FakeHttpClient(
        {
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs": HttpResponse(
                status=200, url="https://boards-api.greenhouse.io/v1/boards/acme/jobs",
                headers={"content-type": "application/json"}, body=b'{"jobs": []}', elapsed_ms=5,
            )
        }
    )
    empty_policy = PolicyHttpClient(transport=empty_http)
    await run_crawl(source=source, connector=connector, uow_factory=factory, http=empty_policy, clock_now=clock.now)

    async with factory() as uow:
        jobs = await uow.jobs.list()
        statuses = {j.get("posting_status") for j in jobs}
        assert "removed" in statuses


@pytest.mark.asyncio
async def test_same_company_is_not_duplicated_across_crawls():
    factory = InMemoryUnitOfWorkFactory()
    http = _board_http()
    policy = PolicyHttpClient(transport=http)
    clock = FakeClock()
    source = SourceConfig(
        id="s1", url="https://boards.greenhouse.io/acme", kind="ats",
        connector_id="ats.greenhouse", ats_tenant_key="acme",
    )
    async with factory() as uow:
        await uow.sources.add(source)
        await uow.commit()

    connector = GreenhouseConnector()
    await run_crawl(source=source, connector=connector, uow_factory=factory, http=policy, clock_now=clock.now)
    await run_crawl(source=source, connector=connector, uow_factory=factory, http=policy, clock_now=clock.now)

    async with factory() as uow:
        companies = await uow.companies.list()
        jobs = await uow.jobs.list()
        assert len(companies) == 1
        assert {j["company_id"] for j in jobs} == {companies[0]["id"]}


@pytest.mark.asyncio
async def test_changed_posting_appends_a_snapshot():
    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock()
    source = SourceConfig(
        id="s1", url="https://boards.greenhouse.io/acme", kind="ats",
        connector_id="ats.greenhouse", ats_tenant_key="acme",
    )
    async with factory() as uow:
        await uow.sources.add(source)
        await uow.commit()

    first = _StaticConnector("Backend Engineer", "Original description")
    await run_crawl(
        source=source, connector=first, uow_factory=factory,
        http=PolicyHttpClient(transport=FakeHttpClient()), clock_now=clock.now,
    )
    second = _StaticConnector("Backend Engineer", "Updated description")
    await run_crawl(
        source=source, connector=second, uow_factory=factory,
        http=PolicyHttpClient(transport=FakeHttpClient()), clock_now=clock.now,
    )

    async with factory() as uow:
        jobs = await uow.jobs.list()
        assert len(jobs) == 1
        snapshots = await uow.snapshots.list_for_job(jobs[0]["id"])
        assert len(snapshots) == 2


@pytest.mark.asyncio
async def test_same_posting_under_a_new_url_creates_an_alias():
    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock()
    source = SourceConfig(
        id="s1", url="https://boards.greenhouse.io/acme", kind="ats",
        connector_id="ats.greenhouse", ats_tenant_key="acme",
    )
    async with factory() as uow:
        await uow.sources.add(source)
        await uow.commit()

    first = _StaticConnector("Backend Engineer", "Body", url="https://boards.greenhouse.io/acme/jobs/1")
    await run_crawl(
        source=source, connector=first, uow_factory=factory,
        http=PolicyHttpClient(transport=FakeHttpClient()), clock_now=clock.now,
    )
    moved = _StaticConnector("Backend Engineer", "Body", url="https://boards.greenhouse.io/acme/jobs/1?src=linkedin")
    await run_crawl(
        source=source, connector=moved, uow_factory=factory,
        http=PolicyHttpClient(transport=FakeHttpClient()), clock_now=clock.now,
    )

    async with factory() as uow:
        jobs = await uow.jobs.list()
        assert len(jobs) == 1
        aliases = await uow.aliases.list_for_job(jobs[0]["id"])
        assert len(aliases) == 1
        assert aliases[0]["alias_url"].endswith("?src=linkedin")


@pytest.mark.asyncio
async def test_failing_source_is_isolated():
    factory = InMemoryUnitOfWorkFactory()
    http = FakeHttpClient(
        {
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs": HttpResponse(
                status=200,
                url="https://boards-api.greenhouse.io/v1/boards/acme/jobs",
                headers={"content-type": "text/plain"},
                body=b"not json",
                elapsed_ms=5,
            )
        }
    )
    policy = PolicyHttpClient(transport=http)
    clock = FakeClock()
    source = SourceConfig(
        id="s1", url="https://boards.greenhouse.io/acme", kind="ats",
        connector_id="ats.greenhouse", ats_tenant_key="acme",
    )
    async with factory() as uow:
        await uow.sources.add(source)
        await uow.commit()

    connector = GreenhouseConnector()
    stats = await run_crawl(
        source=source, connector=connector, uow_factory=factory,
        http=policy, clock_now=clock.now,
    )
    assert "error" in stats
    async with factory() as uow:
        runs = await uow.crawl_runs.list()
        assert runs[0]["status"] == "failed"
