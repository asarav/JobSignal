"""Integration: crawl runner creates jobs, skips unchanged ones, isolates failures."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from app.application.config import AppConfig
from app.application.sources import _refetch_allowed, run_crawl
from app.domain.models.common import Err, Ok, PortError
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpResponse
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig
from app.infrastructure.http.policy_client import PolicyHttpClient
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from app.plugins.ats.greenhouse import GreenhouseConnector
from app.plugins.ats.posting_check import invalid_posting
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

    async def fetch_job(self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext):

        return Ok(
            RawJob(
                ref=ref,
                title=self._title,
                body_text=self._description,
                body_html=None,
                payload={},
            )
        )


class _DeadPostingConnector:
    """A connector whose postings all answer with a settled not-found page."""

    id = "ats.static"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(list_jobs=True, get_job=True)

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        return MatchResult(matched=True, confidence=1.0, ats_hint="static")

    async def list_jobs(self, source: SourceConfig, ctx: ExecutionContext):
        async def gen() -> AsyncIterator[RawJobRef]:
            yield RawJobRef(
                source_id=source.id,
                url="https://acme.taleo.net/careersection/2/jobdetail.ftl?job=9",
            )

        return gen()

    async def fetch_job(self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext):
        return Err(invalid_posting(self.id, ref.url, "page says 'job not found'"))


@pytest.mark.asyncio
async def test_a_dead_posting_is_counted_by_its_reason():
    """``errors: 1`` alone cannot tell a retired posting from a rate limit."""
    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock()
    source = SourceConfig(
        id="s1",
        url="https://acme.taleo.net/careersection/2/jobsearch.ftl?lang=en",
        kind="ats",
        connector_id="ats.taleo",
    )
    async with factory() as uow:
        await uow.sources.add(source)
        await uow.commit()

    stats = await run_crawl(
        source=source,
        connector=_DeadPostingConnector(),  # type: ignore[arg-type]
        uow_factory=factory,
        http=PolicyHttpClient(transport=FakeHttpClient()),
        clock_now=clock.now,
    )

    # A settled dead page counts as *gone*, not as a failed run: it must not
    # hold back the not-seen rule (see the removal tests below).
    assert stats["gone"] == 1
    assert stats["errors"] == 0
    assert stats["errors_by_code"] == {"connector.invalid_response": 1}
    samples = stats["error_samples"]
    assert isinstance(samples, list)
    assert samples[0]["code"] == "connector.invalid_response"
    assert str(samples[0]["url"]).endswith("job=9")


class _UnreachablePostingConnector(_DeadPostingConnector):
    """A connector whose postings fail in a way a retry could fix."""

    async def fetch_job(self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext):
        return Err(PortError(code="http.server_error", message="HTTP 503", retryable=True))


@pytest.mark.asyncio
async def test_an_expired_posting_does_not_block_removal():
    """A dead posting settles the posting, not the run.

    Counting it as a run error meant one expired posting silently disabled the
    not-seen rule, so the source kept stale postings forever.
    """
    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock()
    source = SourceConfig(
        id="s1", url="https://acme.example/careers", kind="ats", connector_id="ats.static"
    )
    async with factory() as uow:
        await uow.sources.add(source)
        await uow.commit()

    await run_crawl(
        source=source,
        connector=_StaticConnector("Backend Engineer", "Build APIs."),
        uow_factory=factory,
        http=PolicyHttpClient(transport=FakeHttpClient()),
        clock_now=clock.now,
    )

    # The board now lists only a posting that has expired, and the stored one
    # is no longer listed at all: two clean listings retire it.
    stats: dict[str, object] = {}
    for _ in range(2):
        clock.advance(25 * 3600)
        stats = await run_crawl(
            source=source,
            connector=_DeadPostingConnector(),  # type: ignore[arg-type]
            uow_factory=factory,
            http=PolicyHttpClient(transport=FakeHttpClient()),
            clock_now=clock.now,
        )

    assert stats["gone"] == 1
    assert stats["errors"] == 0, "an expired posting is not a failed run"
    assert stats["removed"] == 1
    async with factory() as uow:
        statuses = {job.get("posting_status") for job in await uow.jobs.list()}
        runs = await uow.crawl_runs.list()
    assert statuses == {"removed"}
    assert runs[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_an_unreachable_posting_still_holds_removal_back():
    """The opposite case has to keep working: unknown means leave things alone."""
    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock()
    source = SourceConfig(
        id="s1", url="https://acme.example/careers", kind="ats", connector_id="ats.static"
    )
    async with factory() as uow:
        await uow.sources.add(source)
        await uow.commit()

    await run_crawl(
        source=source,
        connector=_StaticConnector("Backend Engineer", "Build APIs."),
        uow_factory=factory,
        http=PolicyHttpClient(transport=FakeHttpClient()),
        clock_now=clock.now,
    )

    stats: dict[str, object] = {}
    for _ in range(2):
        clock.advance(25 * 3600)
        stats = await run_crawl(
            source=source,
            connector=_UnreachablePostingConnector(),  # type: ignore[arg-type]
            uow_factory=factory,
            http=PolicyHttpClient(transport=FakeHttpClient()),
            clock_now=clock.now,
        )

    assert stats["errors"] == 1
    assert stats.get("removed", 0) == 0
    async with factory() as uow:
        statuses = {job.get("posting_status") for job in await uow.jobs.list()}
        runs = await uow.crawl_runs.list()
    assert "removed" not in statuses, "an unreachable posting must not retire jobs"
    assert {str(run["status"]) for run in runs} == {"completed", "completed_with_errors"}


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
    # Two consecutive successful full listings, >= 24 h apart, are required
    # before a missing posting is marked removed (N=2 not-seen rule).
    clock.advance(25 * 3600)
    await run_crawl(source=source, connector=connector, uow_factory=factory, http=empty_policy, clock_now=clock.now)
    clock.advance(25 * 3600)
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
async def test_crawl_extracts_fields_from_html_through_the_pipeline():
    from app.application.extraction import ExtractionPipeline
    from app.plugins.extraction import STRATEGY_CLASSES

    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock()
    source = SourceConfig(
        id="s1", url="https://careers.example.com/jobs/1", kind="career_site",
        connector_id="ats.career_site",
    )
    async with factory() as uow:
        await uow.sources.add(source)
        await uow.commit()

    html = """
    <html><head><script type="application/ld+json">
    {"@type":"JobPosting","title":"Reliability Engineer",
     "description":"Keep the platform healthy.",
     "hiringOrganization":{"name":"Example Co"}}
    </script></head><body></body></html>
    """

    class _HtmlConnector(_StaticConnector):
        async def fetch_job(self, ref, source, ctx):
            from app.domain.models.common import Ok

            return Ok(
                RawJob(ref=ref, title=None, body_text=None, body_html=html, payload={})
            )

    pipeline = ExtractionPipeline([cls() for cls in STRATEGY_CLASSES if cls.id != "extract.demo"])
    stats = await run_crawl(
        source=source, connector=_HtmlConnector("ignored", "ignored"), uow_factory=factory,
        http=PolicyHttpClient(transport=FakeHttpClient()), clock_now=clock.now, pipeline=pipeline,
    )

    assert stats["new"] == 1
    async with factory() as uow:
        jobs = await uow.jobs.list()
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Reliability Engineer"
        assert "Keep the platform healthy." in jobs[0]["description_text"]


@pytest.mark.asyncio
async def test_schema_drift_does_not_mark_jobs_removed():
    """A renamed upstream field fails the crawl instead of wiping the source."""
    from app.domain.models.common import PortError

    class _DriftConnector(_StaticConnector):
        async def list_jobs(self, source, ctx):
            async def gen():
                raise PortError(
                    code="connector.schema_drift",
                    message="listing response has no expected list",
                )
                yield  # pragma: no cover - defines an async generator

            return gen()

    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock()
    source = SourceConfig(
        id="s1", url="https://boards.greenhouse.io/acme", kind="ats",
        connector_id="ats.greenhouse", ats_tenant_key="acme",
    )
    async with factory() as uow:
        await uow.sources.add(source)
        await uow.commit()

    http = PolicyHttpClient(transport=FakeHttpClient())
    await run_crawl(
        source=source,
        connector=_StaticConnector("Backend Engineer", "Body"),
        uow_factory=factory,
        http=http,
        clock_now=clock.now,
    )
    clock.advance(25 * 3600)
    stats = await run_crawl(
        source=source,
        connector=_DriftConnector("ignored", "ignored"),
        uow_factory=factory,
        http=http,
        clock_now=clock.now,
    )

    assert "error" in stats
    async with factory() as uow:
        jobs = await uow.jobs.list()
        assert [j.get("posting_status") for j in jobs] == ["active"]


class _EmptyConnector(_StaticConnector):
    async def list_jobs(self, source, ctx):
        async def gen():
            if False:  # pragma: no cover - keeps this an async generator
                yield RawJobRef(source_id=source.id, url="https://example.com/none")

        return gen()


@pytest.mark.asyncio
async def test_crawl_honours_the_configured_not_seen_threshold():
    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock()
    source = SourceConfig(
        id="s1", url="https://boards.greenhouse.io/acme", kind="ats",
        connector_id="ats.greenhouse", ats_tenant_key="acme",
    )
    async with factory() as uow:
        await uow.sources.add(source)
        await uow.commit()

    http = PolicyHttpClient(transport=FakeHttpClient())
    await run_crawl(
        source=source,
        connector=_StaticConnector("Backend Engineer", "Body"),
        uow_factory=factory,
        http=http,
        clock_now=clock.now,
    )

    # Three consecutive successful full listings, spaced 25 h apart, are
    # required here instead of the default two.
    config = AppConfig(crawl={"not_seen_threshold": 3})
    for _ in range(2):
        clock.advance(25 * 3600)
        await run_crawl(
            source=source,
            connector=_EmptyConnector("ignored", "ignored"),
            uow_factory=factory,
            http=http,
            clock_now=clock.now,
            config=config,
        )
        async with factory() as uow:
            job = (await uow.jobs.list())[0]
            assert job.get("posting_status") != "removed"

    clock.advance(25 * 3600)
    await run_crawl(
        source=source,
        connector=_EmptyConnector("ignored", "ignored"),
        uow_factory=factory,
        http=http,
        clock_now=clock.now,
        config=config,
    )
    async with factory() as uow:
        assert (await uow.jobs.list())[0].get("posting_status") == "removed"


def test_refetch_floor_is_read_from_the_crawl_config():
    from datetime import UTC, datetime

    now = datetime(2026, 11, 21, 12, tzinfo=UTC)
    config = AppConfig(crawl={"min_refetch_hours": 6})
    assert _refetch_allowed({"fetched_at": "2026-11-21T10:00:00Z"}, now, config) is False
    assert _refetch_allowed({"fetched_at": "2026-11-21T05:00:00Z"}, now, config) is True


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
