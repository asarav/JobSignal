"""Identity resolution and repost events at ingestion (spec §7.3 / §19.6)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from app.application.commands.jobs import JobsUseCase
from app.application.sources import run_crawl
from app.domain.models.common import Ok
from app.domain.models.context import ExecutionContext
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import (
    ConnectorCapabilities,
    MatchResult,
    PageSniff,
    SourceConfig,
)
from app.infrastructure.http.policy_client import PolicyHttpClient
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from tests.support.containers import build_test_container
from tests.support.fakes import FakeClock, FakeHttpClient


class _PostingConnector:
    id = "ats.static"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(list_jobs=True, get_job=True)

    def __init__(
        self,
        *,
        url: str,
        posting_id: str,
        description: str,
        title: str = "Backend Engineer",
        location: str | None = None,
        posted_at: str | None = None,
    ) -> None:
        self._url = url
        self._posting_id = posting_id
        self._description = description
        self._title = title
        self._location = location
        self._posted_at = posted_at

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        return MatchResult(matched=True, confidence=1.0, ats_hint="static")

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            yield RawJobRef(
                source_id=source.id,
                url=self._url,
                source_posting_id=self._posting_id,
            )

        return gen()

    async def fetch_job(self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext):
        return Ok(
            RawJob(
                ref=ref,
                title=self._title,
                body_text=self._description,
                body_html=None,
                payload={
                    **({"location": self._location} if self._location else {}),
                    **({"posted_at": self._posted_at} if self._posted_at else {}),
                },
            )
        )


def _source() -> SourceConfig:
    return SourceConfig(
        id="s1",
        url="https://boards.greenhouse.io/acme",
        kind="ats",
        connector_id="ats.static",
        ats_tenant_key="acme",
    )


async def _crawl(factory, clock, connector) -> dict[str, object]:
    return await run_crawl(
        source=_source(),
        connector=connector,
        uow_factory=factory,
        http=PolicyHttpClient(transport=FakeHttpClient()),
        clock_now=clock.now,
    )


@pytest.mark.asyncio
async def test_same_job_at_a_new_url_attaches_an_alias_and_records_an_event():
    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock(datetime(2026, 11, 1, tzinfo=UTC))
    first = _PostingConnector(
        url="https://boards.greenhouse.io/acme/jobs/1",
        posting_id="1",
        description="Build reliable backend services with python and postgres.",
    )
    assert (await _crawl(factory, clock, first))["new"] == 1

    moved = _PostingConnector(
        url="https://boards.greenhouse.io/acme/jobs/2",
        posting_id="2",
        description="Build reliable backend services with python and postgres.",
    )
    stats = await _crawl(factory, clock, moved)
    assert stats["new"] == 0
    assert stats["skipped"] == 1

    async with factory() as uow:
        jobs = await uow.jobs.list()
        assert len(jobs) == 1
        aliases = await uow.aliases.list_for_job(str(jobs[0]["id"]))
        assert [alias["alias_url"] for alias in aliases] == [
            "https://boards.greenhouse.io/acme/jobs/2"
        ]
        events = await uow.dedup.list_repost_events(str(jobs[0]["id"]))
        assert len(events) == 1
        assert events[0]["class"] in {"exact_duplicate", "reposted_identical"}
        assert events[0]["url_changed"] == 1


@pytest.mark.asyncio
async def test_a_material_edit_at_a_new_url_updates_the_same_job():
    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock(datetime(2026, 11, 1, tzinfo=UTC))
    description = (
        "We are hiring a backend engineer to build reliable services with python, "
        "postgres and kubernetes for a growing platform team."
    )
    await _crawl(
        factory,
        clock,
        _PostingConnector(
            url="https://boards.greenhouse.io/acme/jobs/1",
            posting_id="1",
            description=description,
        ),
    )
    edited = description + " You will also own the on-call rotation."
    stats = await _crawl(
        factory,
        clock,
        _PostingConnector(
            url="https://boards.greenhouse.io/acme/jobs/2",
            posting_id="2",
            description=edited,
        ),
    )
    assert stats["updated"] == 1

    async with factory() as uow:
        jobs = await uow.jobs.list()
        assert len(jobs) == 1
        job_id = str(jobs[0]["id"])
        snapshots = await uow.snapshots.list_for_job(job_id)
        assert len(snapshots) == 2
        events = await uow.dedup.list_repost_events(job_id)
        assert events and events[0]["class"] == "materially_updated"
        assert events[0]["material"] == 1


@pytest.mark.asyncio
async def test_similar_postings_stay_separate_and_are_linked():
    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock(datetime(2026, 11, 1, tzinfo=UTC))
    await _crawl(
        factory,
        clock,
        _PostingConnector(
            url="https://boards.greenhouse.io/acme/jobs/1",
            posting_id="1",
            description="Write python services and maintain the data platform.",
            location="New York, NY",
        ),
    )
    stats = await _crawl(
        factory,
        clock,
        _PostingConnector(
            url="https://boards.greenhouse.io/acme/jobs/2",
            posting_id="2",
            description="Write python services and maintain the data platform.",
            location="London, UK",
        ),
    )
    assert stats["new"] == 1

    async with factory() as uow:
        jobs = await uow.jobs.list()
        assert len(jobs) == 2
        relations = await uow.jobs.list_relations(str(jobs[-1]["id"]))
        assert [relation["relation"] for relation in relations] == ["similar"]


@pytest.mark.asyncio
async def test_manual_merge_and_unmerge_override_automation():
    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock(datetime(2026, 11, 1, tzinfo=UTC))
    await _crawl(
        factory,
        clock,
        _PostingConnector(
            url="https://boards.greenhouse.io/acme/jobs/1",
            posting_id="1",
            description="Write python services and maintain the data platform.",
        ),
    )
    await _crawl(
        factory,
        clock,
        _PostingConnector(
            url="https://boards.greenhouse.io/acme/jobs/2",
            posting_id="2",
            description="Own accounting, invoicing and payroll operations.",
            title="Accountant",
        ),
    )
    container = build_test_container(uow_factory=factory, clock=clock)
    jobs = JobsUseCase(container)

    async with factory() as uow:
        rows = await uow.jobs.list()
        primary, duplicate = str(rows[0]["id"]), str(rows[1]["id"])

    await jobs.merge(primary, duplicate)
    async with factory() as uow:
        relations = await uow.jobs.list_relations(primary)
        assert any(relation["relation"] == "user_merged" for relation in relations)
        aliases = await uow.aliases.list_for_job(primary)
        assert any(
            alias["alias_url"] == "https://boards.greenhouse.io/acme/jobs/2"
            for alias in aliases
        )

    duplicates = await jobs.list_duplicates()
    assert {job.job_id for job in duplicates.jobs} == {primary, duplicate}
    assert duplicate in duplicates.jobs[0].related_job_ids or duplicate in (
        duplicates.jobs[1].related_job_ids
    )

    await jobs.unmerge(primary, duplicate)
    async with factory() as uow:
        relations = await uow.jobs.list_relations(primary)
        assert not any(relation["relation"] == "user_merged" for relation in relations)
        assert any(
            relation["relation"] == "user_unmerged" for relation in relations
        )
        aliases = await uow.aliases.list_for_job(primary)
        assert not aliases


LONG_DESCRIPTION = (
    "We are hiring a backend engineer to build reliable services with python, "
    "postgres and kubernetes. You will design APIs, review code, improve "
    "observability, and work with product and design partners to ship features "
    "that customers depend on every day. Our team values clear writing, careful "
    "testing, and small changes that are easy to review and roll back safely."
)


@pytest.mark.asyncio
async def test_a_small_edit_is_a_repost_that_is_not_material():
    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock(datetime(2026, 11, 1, tzinfo=UTC))
    await _crawl(
        factory,
        clock,
        _PostingConnector(
            url="https://boards.greenhouse.io/acme/jobs/1",
            posting_id="1",
            description=LONG_DESCRIPTION,
        ),
    )
    stats = await _crawl(
        factory,
        clock,
        _PostingConnector(
            url="https://boards.greenhouse.io/acme/jobs/2",
            posting_id="2",
            description=LONG_DESCRIPTION + " Fully remote.",
        ),
    )
    assert stats["skipped"] == 1

    async with factory() as uow:
        jobs = await uow.jobs.list()
        assert len(jobs) == 1
        events = await uow.dedup.list_repost_events(str(jobs[0]["id"]))
        assert events and events[0]["class"] == "reposted_identical"
        assert events[0]["material"] == 0


@pytest.mark.asyncio
async def test_a_new_skill_in_the_requirements_is_a_material_change():
    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock(datetime(2026, 11, 1, tzinfo=UTC))
    await _crawl(
        factory,
        clock,
        _PostingConnector(
            url="https://boards.greenhouse.io/acme/jobs/1",
            posting_id="1",
            description=LONG_DESCRIPTION,
        ),
    )
    stats = await _crawl(
        factory,
        clock,
        _PostingConnector(
            url="https://boards.greenhouse.io/acme/jobs/2",
            posting_id="2",
            description=LONG_DESCRIPTION.replace("kubernetes", "terraform"),
        ),
    )
    assert stats["updated"] == 1

    async with factory() as uow:
        jobs = await uow.jobs.list()
        assert len(jobs) == 1
        events = await uow.dedup.list_repost_events(str(jobs[0]["id"]))
        assert events and events[0]["class"] == "materially_updated"
        assert events[0]["material"] == 1


@pytest.mark.asyncio
async def test_a_later_posting_date_on_identical_content_is_a_reset():
    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock(datetime(2026, 11, 1, tzinfo=UTC))
    await _crawl(
        factory,
        clock,
        _PostingConnector(
            url="https://boards.greenhouse.io/acme/jobs/1",
            posting_id="1",
            description=LONG_DESCRIPTION,
            posted_at="2026-11-01T00:00:00Z",
        ),
    )
    await _crawl(
        factory,
        clock,
        _PostingConnector(
            url="https://boards.greenhouse.io/acme/jobs/2",
            posting_id="2",
            description=LONG_DESCRIPTION,
            posted_at="2026-11-15T00:00:00Z",
        ),
    )
    async with factory() as uow:
        jobs = await uow.jobs.list()
        assert len(jobs) == 1
        events = await uow.dedup.list_repost_events(str(jobs[0]["id"]))
        assert events and events[0]["class"] in {
            "exact_duplicate",
            "reposted_identical",
        }
        assert events[0]["posted_date_reset"] == 1
        assert events[0]["material"] == 0


@pytest.mark.asyncio
async def test_automation_does_not_re_merge_a_manual_unmerge():
    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock(datetime(2026, 11, 1, tzinfo=UTC))
    first = _PostingConnector(
        url="https://boards.greenhouse.io/acme/jobs/1",
        posting_id="1",
        description=LONG_DESCRIPTION,
    )
    duplicate = _PostingConnector(
        url="https://boards.greenhouse.io/acme/jobs/2",
        posting_id="2",
        description=LONG_DESCRIPTION,
        title="Accountant",
    )
    await _crawl(factory, clock, first)
    await _crawl(factory, clock, duplicate)

    container = build_test_container(uow_factory=factory, clock=clock)
    jobs = JobsUseCase(container)
    async with factory() as uow:
        rows = await uow.jobs.list()
        primary, other = str(rows[0]["id"]), str(rows[1]["id"])

    await jobs.merge(primary, other)
    await jobs.unmerge(primary, other)
    # The automation sees the same posting again; it must stay separate.
    await _crawl(factory, clock, duplicate)

    async with factory() as uow:
        # Both postings remain separate jobs; automation must not re-attach.
        assert len(await uow.jobs.list()) == 2
        assert not await uow.aliases.list_for_job(primary)
        relations = await uow.jobs.list_relations(primary)
        assert not any(r["relation"] == "user_merged" for r in relations)
        assert any(r["relation"] == "user_unmerged" for r in relations)


@pytest.mark.asyncio
async def test_removed_jobs_appear_in_the_seen_view():
    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock(datetime(2026, 11, 1, tzinfo=UTC))
    await _crawl(
        factory,
        clock,
        _PostingConnector(
            url="https://boards.greenhouse.io/acme/jobs/1",
            posting_id="1",
            description="Build backend services.",
        ),
    )
    async with factory() as uow:
        job_id = str((await uow.jobs.list())[0]["id"])
        job = await uow.jobs.get(job_id)
        assert job is not None
        job["posting_status"] = "removed"
        await uow.commit()

    container = build_test_container(uow_factory=factory, clock=clock)
    seen = await JobsUseCase(container).list_seen()
    assert [job.job_id for job in seen.jobs] == [job_id]
    assert seen.jobs[0].reason == "posting_removed"
