"""IPC round trips for company merge and keyword insights."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.domain.models.posting import RiskClass
from tests.support.containers import build_test_container

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _job(job_id: str, company_id: str, text: str = "python and sql") -> dict[str, Any]:
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


async def _seed(container) -> None:
    async with container.uow_factory() as uow:
        await uow.companies.upsert("co_a", "Acme")
        await uow.companies.upsert("co_b", "Acme Inc")
        container.uow_factory.jobs["job_1"] = _job("job_1", "co_b")
        container.uow_factory.jobs["job_2"] = _job("job_2", "co_a", text="sql only")
        await uow.commit()


async def _call(dispatcher, method: str, params: dict[str, Any]) -> dict[str, Any]:
    response = await dispatcher.handle_request(
        {"id": "req_1", "method": method, "params": params}
    )
    assert response["ok"] is True, response
    return response["result"]


@pytest.mark.asyncio
async def test_company_merge_round_trip_over_ipc() -> None:
    from app.worker.main import _build_dispatcher

    container = build_test_container()
    await _seed(container)
    dispatcher = _build_dispatcher(container)

    merged = await _call(
        dispatcher, "companies.merge", {"company_id": "co_b", "into_id": "co_a"}
    )
    assert merged["changed"] is True
    assert merged["survivor_id"] == "co_a"

    listing = await _call(dispatcher, "companies.list", {})
    assert listing["total"] == 1
    assert listing["companies"][0]["merged_ids"] == ["co_b"]

    history = await _call(dispatcher, "companies.get_merge_history", {"company_id": "co_b"})
    assert history["total"] == 1
    assert history["records"][0]["action"] == "merge"

    undone = await _call(dispatcher, "companies.unmerge", {"company_id": "co_b"})
    assert undone["changed"] is True
    assert (await _call(dispatcher, "companies.list", {}))["total"] == 2


@pytest.mark.asyncio
async def test_keyword_insights_round_trip_over_ipc() -> None:
    from app.application.intelligence import RiskEvaluationService
    from app.worker.main import _build_dispatcher

    container = build_test_container()
    await _seed(container)
    await RiskEvaluationService(container).evaluate(scope="all")
    dispatcher = _build_dispatcher(container)

    prevalence = await _call(dispatcher, "keywords.prevalence", {})
    assert prevalence["n_jobs"] == 2
    assert prevalence["small_sample"] is True

    coverage = await _call(dispatcher, "keywords.coverage", {"profile_id": "default"})
    assert coverage["scope"] == "global"
    assert "coverage_ratio" in coverage

    backfill = await _call(dispatcher, "keywords.backfill", {"limit": 10})
    assert backfill["processed"] == 0, "the pipeline already stored these revisions"
    assert backfill["remaining"] == 0


@pytest.mark.asyncio
async def test_company_merge_errors_are_envelopes_not_crashes() -> None:
    from app.worker.main import _build_dispatcher

    container = build_test_container()
    await _seed(container)
    dispatcher = _build_dispatcher(container)

    response = await dispatcher.handle_request(
        {
            "id": "req_2",
            "method": "companies.merge",
            "params": {"company_id": "co_a", "into_id": "co_a"},
        }
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "validation.invalid_params"


@pytest.mark.asyncio
async def test_a_risk_class_is_still_a_valid_merge_input() -> None:
    # Guard against accidental coupling: merging never touches risk history.
    from app.worker.main import _build_dispatcher

    container = build_test_container()
    await _seed(container)
    dispatcher = _build_dispatcher(container)
    await _call(dispatcher, "companies.merge", {"company_id": "co_b", "into_id": "co_a"})

    async with container.uow_factory() as uow:
        assert await uow.intelligence.assessments.latest("job_1") is None
    assert RiskClass.LIKELY_ACTIVE.value == "likely_active"
