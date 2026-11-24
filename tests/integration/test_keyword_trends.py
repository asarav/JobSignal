"""Keyword trends: per-period counts, honest gaps, merged scope, batched reads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.commands.companies import CompaniesUseCase
from app.application.dto import CompanyMergeParams
from app.application.keywords import KeywordInsightsService, KeywordPersistenceService
from tests.support.containers import build_test_container

NOW = datetime(2026, 1, 1, tzinfo=UTC)
RECENT = datetime(2025, 12, 10, tzinfo=UTC)
OLDER = datetime(2025, 11, 12, tzinfo=UTC)


def _job(
    job_id: str,
    *,
    company_id: str = "co_a",
    text: str = "python and sql",
) -> dict[str, Any]:
    return {
        "id": job_id,
        "company_id": company_id,
        "source_id": "src_1",
        "title": "Data Engineer",
        "posting_status": "active",
        "canonical_url": f"https://acme.test/jobs/{job_id}",
        "description_text": text,
        "content_hash": f"hash-{job_id}-{len(text)}",
        "first_seen_at": NOW.isoformat(),
        "last_seen_at": NOW.isoformat(),
        "fetched_at": NOW.isoformat(),
    }


async def _seed(container, jobs: list[dict[str, Any]]) -> None:
    async with container.uow_factory() as uow:
        await uow.companies.upsert("co_a", "Acme")
        await uow.companies.upsert("co_b", "Acme Inc")
        for job in jobs:
            container.uow_factory.jobs[str(job["id"])] = job
        await uow.commit()


@pytest.mark.asyncio
async def test_trends_report_counts_and_hide_rates_below_the_threshold() -> None:
    container = build_test_container()
    jobs = [_job(f"job_{index}") for index in range(12)]
    jobs += [_job(f"old_{index}", text="python only") for index in range(4)]
    await _seed(container, jobs)

    async with container.uow_factory() as uow:
        service = KeywordPersistenceService(container)
        for job in jobs[:12]:
            await service.persist_for_job(uow, job, RECENT)
        for job in jobs[12:]:
            await service.persist_for_job(uow, job, OLDER)
        await uow.commit()

    payload = await KeywordInsightsService(container).trends(periods=4)

    assert payload["bucket"] == "month"
    by_period = {row["period"]: row for row in payload["periods"]}
    recent = by_period["2025-12"]
    assert recent["n_jobs"] == 12
    assert recent["insufficient"] is False
    recent_values = {row["keyword_id"]: row for row in recent["keywords"]}
    assert recent_values["python"]["jobs"] == 12
    assert recent_values["python"]["prevalence"] == pytest.approx(1.0)

    older = by_period["2025-11"]
    assert older["n_jobs"] == 4
    assert older["insufficient"] is True
    older_values = {row["keyword_id"]: row for row in older["keywords"]}
    assert older_values["python"]["jobs"] == 4
    assert older_values["python"]["prevalence"] is None
    assert older_values["python"]["insufficient"] is True

    empty = [row for row in payload["periods"] if row["n_jobs"] == 0]
    assert empty, "periods without postings stay in the series"
    for row in empty:
        assert row["no_data"] is True
        assert row["insufficient"] is True
        assert all(value["prevalence"] is None for value in row["keywords"])


@pytest.mark.asyncio
async def test_trends_follow_the_company_scope_through_a_merge() -> None:
    container = build_test_container()
    jobs = [_job("job_1", company_id="co_b")] + [
        _job(f"job_{index}", company_id="co_a") for index in range(2, 11)
    ]
    await _seed(container, jobs)
    async with container.uow_factory() as uow:
        service = KeywordPersistenceService(container)
        for job in jobs:
            await service.persist_for_job(uow, job, RECENT)
        await uow.commit()
    await CompaniesUseCase(container).merge(
        CompanyMergeParams(company_id="co_b", into_id="co_a")
    )

    payload = await KeywordInsightsService(container).trends(
        company_id="co_b", periods=2
    )

    assert payload["scope"] == "company"
    assert payload["company_id"] == "co_a"
    assert payload["n_jobs"] == 10
    period = {row["period"]: row for row in payload["periods"]}["2025-12"]
    assert period["n_jobs"] == 10


@pytest.mark.asyncio
async def test_trends_use_one_batched_read_per_source() -> None:
    container = build_test_container()
    jobs = [_job(f"job_{index}") for index in range(3)]
    await _seed(container, jobs)
    async with container.uow_factory() as uow:
        service = KeywordPersistenceService(container)
        for job in jobs:
            await service.persist_for_job(uow, job, RECENT)
        await uow.commit()

    calls = {"revisions": 0, "rows": 0}
    repo = container.uow_factory.keywords
    original_revisions = repo.latest_revisions_for_jobs
    original_rows = repo.keyword_rows_for_jobs

    async def counting_revisions(job_ids):
        calls["revisions"] += 1
        return await original_revisions(job_ids)

    async def counting_rows(job_ids):
        calls["rows"] += 1
        return await original_rows(job_ids)

    repo.latest_revisions_for_jobs = counting_revisions  # type: ignore[method-assign]
    repo.keyword_rows_for_jobs = counting_rows  # type: ignore[method-assign]

    await KeywordInsightsService(container).trends(periods=3)

    assert calls == {"revisions": 1, "rows": 1}


@pytest.mark.asyncio
async def test_an_unknown_bucket_is_rejected() -> None:
    container = build_test_container()
    with pytest.raises(Exception) as excinfo:
        await KeywordInsightsService(container).trends(bucket="day")
    assert getattr(excinfo.value, "code", "") == "validation.invalid_params"
