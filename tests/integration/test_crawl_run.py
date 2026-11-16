"""Integration: crawl runner creates jobs, skips unchanged ones, isolates failures."""

from __future__ import annotations

import json

import pytest
from app.application.sources import run_crawl
from app.domain.models.http import HttpResponse
from app.domain.models.source import SourceConfig
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
