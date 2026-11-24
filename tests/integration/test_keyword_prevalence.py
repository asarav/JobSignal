"""Keywords persist through the pipeline; prevalence never invents a trend."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.commands.companies import CompaniesUseCase
from app.application.dto import CompanyMergeParams
from app.application.intelligence import RiskEvaluationService
from app.application.keywords import KeywordInsightsService, KeywordPersistenceService
from tests.support.containers import build_test_container

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _job(
    job_id: str, *, company_id: str = "co_a", text: str = "python and sql"
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


def _twelve_jobs() -> list[dict[str, Any]]:
    jobs = []
    for index in range(12):
        text = "python and sql" if index < 9 else "sql only"
        jobs.append(_job(f"job_{index}", text=text))
    return jobs


@pytest.mark.asyncio
async def test_the_pipeline_persists_keywords_and_computes_prevalence() -> None:
    container = build_test_container()
    await _seed(container, _twelve_jobs())

    result = await RiskEvaluationService(container).evaluate(scope="all")
    assert result["evaluated"] == 12

    payload = await KeywordInsightsService(container).prevalence()
    assert payload["n_jobs"] == 12
    assert payload["small_sample"] is False
    by_keyword = {row["keyword_id"]: row for row in payload["keywords"]}
    assert by_keyword["python"]["jobs"] == 9
    assert by_keyword["python"]["prevalence"] == pytest.approx(0.75)
    assert by_keyword["sql"]["prevalence"] == pytest.approx(1.0)
    assert by_keyword["python"]["insufficient"] is False
    assert by_keyword["python"]["interval"][0] < 0.75 < by_keyword["python"]["interval"][1]


@pytest.mark.asyncio
async def test_a_posting_with_no_extractable_text_is_missing_not_zero() -> None:
    container = build_test_container()
    await _seed(
        container,
        [
            _job("job_1", text="python"),
            _job("job_2", text="python and sql"),
            _job("job_3", text=""),
        ],
    )

    await RiskEvaluationService(container).evaluate(scope="all")
    payload = await KeywordInsightsService(container).prevalence()

    assert payload["n_jobs"] == 2, "the empty posting is missing evidence"
    by_keyword = {row["keyword_id"]: row for row in payload["keywords"]}
    assert by_keyword["python"]["jobs"] == 2


@pytest.mark.asyncio
async def test_a_small_sample_shows_counts_but_no_trend() -> None:
    container = build_test_container()
    await _seed(container, [_job("job_1"), _job("job_2", text="sql only")])

    await RiskEvaluationService(container).evaluate(scope="all")
    payload = await KeywordInsightsService(container).prevalence()

    assert payload["n_jobs"] == 2
    assert payload["small_sample"] is True
    assert payload["keywords"]
    assert all(row["insufficient"] for row in payload["keywords"])


@pytest.mark.asyncio
async def test_company_scope_includes_merged_companies() -> None:
    container = build_test_container()
    await _seed(
        container,
        [
            _job("job_1", company_id="co_a", text="python"),
            _job("job_2", company_id="co_b", text="python"),
            _job("job_3", company_id="co_b", text="sql"),
        ],
    )
    await RiskEvaluationService(container).evaluate(scope="all")
    companies = CompaniesUseCase(container)
    await companies.merge(CompanyMergeParams(company_id="co_b", into_id="co_a"))

    payload = await KeywordInsightsService(container).prevalence(company_id="co_b")

    assert payload["scope"] == "company"
    assert payload["company_id"] == "co_a"
    assert payload["n_jobs"] == 3
    by_keyword = {row["keyword_id"]: row for row in payload["keywords"]}
    assert by_keyword["python"]["jobs"] == 2


@pytest.mark.asyncio
async def test_coverage_compares_scope_with_profile_skills() -> None:
    container = build_test_container()
    await _seed(container, _twelve_jobs())
    await RiskEvaluationService(container).evaluate(scope="all")
    async with container.uow_factory() as uow:
        await uow.resumes.set_skill_status("default", "python", "have")
        await uow.commit()

    payload = await KeywordInsightsService(container).coverage()

    assert payload["candidate_skills"] == 1
    assert [row["keyword_id"] for row in payload["covered"]] == ["python"]
    assert [row["keyword_id"] for row in payload["missing"]] == ["sql"]
    assert payload["coverage_ratio"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_backfill_is_resumable() -> None:
    container = build_test_container()
    await _seed(container, [_job(f"job_{index}") for index in range(3)])
    service = KeywordPersistenceService(container)

    first = await service.backfill(limit=2)
    second = await service.backfill(limit=2)

    assert first["processed"] == 2
    assert first["remaining"] == 1
    assert second["processed"] == 1
    assert second["remaining"] == 0


@pytest.mark.asyncio
async def test_re_evaluating_the_same_revision_does_not_duplicate() -> None:
    container = build_test_container()
    await _seed(container, [_job("job_1")])
    service = RiskEvaluationService(container)

    await service.evaluate(job_ids=["job_1"])
    await service.evaluate(job_ids=["job_1"], force=True)

    payload = await KeywordInsightsService(container).prevalence()
    assert payload["n_jobs"] == 1
