"""Merge resolves every company read to the survivor; unmerge restores it."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.commands.applications import ApplicationsUseCase
from app.application.commands.companies import CompaniesUseCase
from app.application.commands.jobs import JobsUseCase
from app.application.dto import (
    ApplicationCreateParams,
    CompanyMergeParams,
    JobFilter,
)
from app.application.intelligence import CompanySummaryService
from app.domain.models.common import PortError
from app.domain.models.posting import CompanySummaryRecord
from tests.support.containers import build_test_container

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _job(job_id: str, company_id: str) -> dict[str, Any]:
    return {
        "id": job_id,
        "company_id": company_id,
        "source_id": "src_1",
        "title": "Data Engineer",
        "posting_status": "active",
        "canonical_url": f"https://acme.test/jobs/{job_id}",
        "description_text": "Own ingestion with python and sql.",
        "first_seen_at": NOW.isoformat(),
        "last_seen_at": NOW.isoformat(),
        "fetched_at": NOW.isoformat(),
    }


async def _seed(container) -> None:
    async with container.uow_factory() as uow:
        await uow.companies.upsert("co_a", "Acme")
        await uow.companies.upsert("co_b", "Acme Inc")
        container.uow_factory.jobs["job_1"] = _job("job_1", "co_b")
        container.uow_factory.jobs["job_2"] = _job("job_2", "co_a")
        await uow.commit()
    await ApplicationsUseCase(container).create(
        ApplicationCreateParams(job_id="job_1", status="saved")
    )
    async with container.uow_factory() as uow:
        await uow.intelligence.company_summaries.add(
            CompanySummaryRecord(
                id="chs_1",
                company_id="co_b",
                computed_at=NOW,
                sample_tier="limited",
                metrics={"n_unique_postings": 1},
            )
        )
        await uow.commit()


@pytest.mark.asyncio
async def test_merge_aggregates_list_detail_jobs_and_summaries() -> None:
    container = build_test_container()
    await _seed(container)
    companies = CompaniesUseCase(container)

    before = await companies.list()
    assert before.total == 2

    merged = await companies.merge(
        CompanyMergeParams(company_id="co_b", into_id="co_a", reason="same employer")
    )
    assert merged.changed is True
    assert merged.survivor_id == "co_a"

    after = await companies.list()
    assert after.total == 1
    survivor = after.companies[0]
    assert survivor.company_id == "co_a"
    assert survivor.job_count == 2
    assert survivor.application_count == 1
    assert survivor.merged_ids == ["co_b"]

    detail = await companies.get_detail("co_b")
    assert detail.company_id == "co_a"
    assert detail.requested_id == "co_b"
    assert detail.job_count == 2
    assert detail.summary is not None, "the merged child's summary still counts"
    assert detail.summary["id"] == "chs_1"

    jobs = await JobsUseCase(container).list(JobFilter(limit=10))
    by_id = {job.job_id: job for job in jobs.jobs}
    assert by_id["job_1"].company_id == "co_a"
    assert by_id["job_1"].company_name == "Acme"


@pytest.mark.asyncio
async def test_merge_is_idempotent_and_audited_once() -> None:
    container = build_test_container()
    await _seed(container)
    companies = CompaniesUseCase(container)

    first = await companies.merge(CompanyMergeParams(company_id="co_b", into_id="co_a"))
    second = await companies.merge(CompanyMergeParams(company_id="co_b", into_id="co_a"))
    history = await companies.merge_history("co_b")

    assert first.changed is True
    assert second.changed is False
    assert history.total == 1
    record = history.records[0]
    assert record["action"] == "merge"
    assert record["survivor_id"] == "co_a"
    assert record["merged_id"] == "co_b"
    assert record["created_by"] == "user"
    assert record["created_at"]
    assert history.currently_merged == {"co_b": "co_a"}


@pytest.mark.asyncio
async def test_unmerge_restores_the_previous_view_exactly() -> None:
    container = build_test_container()
    await _seed(container)
    companies = CompaniesUseCase(container)
    await companies.merge(CompanyMergeParams(company_id="co_b", into_id="co_a"))

    undone = await companies.unmerge("co_b")
    assert undone.changed is True
    assert undone.survivor_id == "co_b"

    listing = await companies.list()
    assert listing.total == 2
    counts = {row.company_id: row.job_count for row in listing.companies}
    assert counts == {"co_a": 1, "co_b": 1}

    detail = await companies.get_detail("co_b")
    assert detail.company_id == "co_b"
    assert detail.requested_id is None
    assert detail.job_count == 1
    assert detail.summary["id"] == "chs_1"

    jobs = await JobsUseCase(container).list(JobFilter(limit=10))
    by_id = {job.job_id: job for job in jobs.jobs}
    assert by_id["job_1"].company_id == "co_b"

    history = await companies.merge_history("co_b")
    assert [record["action"] for record in history.records] == ["unmerge", "merge"]
    assert history.currently_merged == {}


@pytest.mark.asyncio
async def test_unmerge_is_idempotent() -> None:
    container = build_test_container()
    await _seed(container)
    companies = CompaniesUseCase(container)

    first = await companies.unmerge("co_a")
    second = await companies.unmerge("co_a")

    assert first.changed is False
    assert second.changed is False
    assert (await companies.merge_history("co_a")).total == 0


@pytest.mark.asyncio
async def test_a_cycle_is_rejected() -> None:
    container = build_test_container()
    await _seed(container)
    companies = CompaniesUseCase(container)
    await companies.merge(CompanyMergeParams(company_id="co_b", into_id="co_a"))

    with pytest.raises(PortError) as excinfo:
        await companies.merge(CompanyMergeParams(company_id="co_a", into_id="co_b"))
    assert excinfo.value.code == "validation.merge_cycle"


@pytest.mark.asyncio
async def test_a_chain_resolves_to_the_final_survivor() -> None:
    container = build_test_container()
    async with container.uow_factory() as uow:
        for company_id, name in (("co_a", "A"), ("co_b", "B"), ("co_c", "C")):
            await uow.companies.upsert(company_id, name)
        await uow.commit()
    companies = CompaniesUseCase(container)

    await companies.merge(CompanyMergeParams(company_id="co_b", into_id="co_a"))
    await companies.merge(CompanyMergeParams(company_id="co_a", into_id="co_c"))

    detail = await companies.get_detail("co_b")
    assert detail.company_id == "co_c"
    listing = await companies.list()
    assert [row.company_id for row in listing.companies] == ["co_c"]


@pytest.mark.asyncio
async def test_merging_into_an_unknown_company_is_rejected() -> None:
    container = build_test_container()
    await _seed(container)
    with pytest.raises(PortError) as excinfo:
        await CompaniesUseCase(container).merge(
            CompanyMergeParams(company_id="co_b", into_id="missing")
        )
    assert excinfo.value.code == "not_found"


@pytest.mark.asyncio
async def test_company_summaries_only_land_under_the_survivor() -> None:
    container = build_test_container()
    await _seed(container)
    companies = CompaniesUseCase(container)
    await companies.merge(CompanyMergeParams(company_id="co_b", into_id="co_a"))

    written = await CompanySummaryService(container).compute(["co_b"])
    assert written == 0, "a merged child never gets its own summary"
