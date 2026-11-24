"""Company breadth is read-only and counts what it can see."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.commands.applications import ApplicationsUseCase
from app.application.commands.companies import CompaniesUseCase
from app.application.dto import ApplicationCreateParams
from app.domain.models.common import PortError
from tests.support.containers import build_test_container

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _job(job_id: str, company_id: str, status: str = "active") -> dict[str, Any]:
    return {
        "id": job_id,
        "company_id": company_id,
        "source_id": "src_1",
        "title": "Data Engineer",
        "posting_status": status,
        "canonical_url": f"https://acme.test/jobs/{job_id}",
        "description_text": "Own ingestion.",
        "first_seen_at": NOW.isoformat(),
        "fetched_at": NOW.isoformat(),
    }


@pytest.mark.asyncio
async def test_list_counts_postings_and_applications() -> None:
    container = build_test_container()
    async with container.uow_factory() as uow:
        await uow.companies.upsert("co_1", "Acme", domain="acme.test")
        container.uow_factory.jobs["job_1"] = _job("job_1", "co_1")
        container.uow_factory.jobs["job_2"] = _job("job_2", "co_1", status="removed")
        await uow.commit()
    await ApplicationsUseCase(container).create(ApplicationCreateParams(job_id="job_1"))

    result = await CompaniesUseCase(container).list()

    assert result.total == 1
    company = result.companies[0]
    assert company.name == "Acme"
    assert company.job_count == 2
    assert company.active_job_count == 1
    assert company.application_count == 1


@pytest.mark.asyncio
async def test_detail_returns_jobs_and_your_applications() -> None:
    container = build_test_container()
    async with container.uow_factory() as uow:
        await uow.companies.upsert("co_1", "Acme")
        container.uow_factory.jobs["job_1"] = _job("job_1", "co_1")
        await uow.commit()
    await ApplicationsUseCase(container).create(ApplicationCreateParams(job_id="job_1"))

    detail = await CompaniesUseCase(container).get_detail("co_1")

    assert detail.name == "Acme"
    assert detail.job_count == 1
    assert detail.jobs[0].job_id == "job_1"
    assert detail.applications[0]["job_id"] == "job_1"
    assert detail.summary is None


@pytest.mark.asyncio
async def test_an_unknown_company_is_not_found() -> None:
    container = build_test_container()
    with pytest.raises(PortError) as excinfo:
        await CompaniesUseCase(container).get_detail("missing")
    assert excinfo.value.code == "not_found"
