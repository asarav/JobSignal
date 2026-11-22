"""Search filters must see the whole database, not just the first page."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.application.commands.jobs import JobsUseCase
from app.application.dto import JobFilter
from app.domain.models.enums import WorkplaceType
from app.domain.models.source import SourceConfig
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from app.infrastructure.persistence.sqlite.migrator import migrate
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory
from tests.support.containers import build_test_container
from tests.support.fakes import FakeClock


def _job(title: str, workplace: WorkplaceType) -> SimpleNamespace:
    return SimpleNamespace(
        title=title,
        normalized_title=title.lower(),
        description_text="Build things",
        canonical_url=f"https://example.test/{title.replace(' ', '-').lower()}",
        source_posting_id=title,
        workplace_type=workplace,
        employment_type="full_time",
        seniority="mid",
        locations=[],
        salary=None,
        posted_at=None,
        provenance=(),
    )


@pytest.mark.asyncio
async def test_hard_filters_apply_before_pagination():
    factory = InMemoryUnitOfWorkFactory()
    async with factory() as uow:
        # More newest-first rows than the requested page, so the one remote
        # role sits beyond the page the old implementation filtered.
        for index in range(60):
            await uow.jobs.upsert(
                f"job_{index}",
                company_id="co",
                source_id="src",
                job=_job(f"Onsite Role {index}", WorkplaceType.ONSITE),
            )
        await uow.jobs.upsert(
            "job_remote",
            company_id="co",
            source_id="src",
            job=_job("Remote Engineer", WorkplaceType.REMOTE),
        )
        await uow.commit()

    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    container = build_test_container(uow_factory=factory, clock=clock)
    result = await JobsUseCase(container).list(
        JobFilter(filters={"workplace_types": ["remote"]}, limit=5)
    )

    assert [job.job_id for job in result.jobs] == ["job_remote"]
    assert result.total == 1


@pytest.mark.asyncio
async def test_hard_filters_scan_past_ten_thousand_jobs():
    """The old fixed 10,000-row scan silently dropped older matches."""
    factory = InMemoryUnitOfWorkFactory()
    async with factory() as uow:
        for index in range(10_050):
            await uow.jobs.upsert(
                f"job_{index}",
                company_id="co",
                source_id="src",
                job=_job(f"Onsite Role {index}", WorkplaceType.ONSITE),
            )
        await uow.jobs.upsert(
            "job_remote",
            company_id="co",
            source_id="src",
            job=_job("Remote Engineer", WorkplaceType.REMOTE),
        )
        await uow.commit()

    container = build_test_container(uow_factory=factory)
    result = await JobsUseCase(container).list(
        JobFilter(filters={"workplace_types": ["remote"]}, limit=5)
    )

    assert [job.job_id for job in result.jobs] == ["job_remote"]
    assert result.total == 1


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.asyncio
async def test_filtered_keyword_search_pages_until_exhausted(
    tmp_path, backend: str
):
    if backend == "memory":
        factory = InMemoryUnitOfWorkFactory()
    else:
        db = tmp_path / "filter-pages.sqlite"
        await migrate(db)
        factory = SqliteUnitOfWorkFactory(db_path=db, clock_now=FakeClock().now)

    async with factory() as uow:
        await uow.companies.upsert("co", "Co", normalized_name="co")
        await uow.sources.add(
            SourceConfig(id="src", url="https://example.test", kind="career_site")
        )
        for index in range(5):
            await uow.jobs.upsert(
                f"job_{index}",
                company_id="co",
                source_id="src",
                job=_job(f"Onsite Role {index}", WorkplaceType.ONSITE),
            )
        await uow.jobs.upsert(
            "job_remote",
            company_id="co",
            source_id="src",
            job=_job("Role Remote", WorkplaceType.REMOTE),
        )
        await uow.commit()

    container = build_test_container(uow_factory=factory)
    result = await JobsUseCase(container).list(
        JobFilter(
            query="Role",
            filters={"workplace_types": ["remote"]},
            limit=5,
        )
    )

    assert [job.job_id for job in result.jobs] == ["job_remote"]
    assert result.total == 1
