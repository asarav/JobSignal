"""IPC round trips and error envelopes for M10 keyword trends and weight suggestions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.keywords import KeywordPersistenceService
from tests.support.containers import build_test_container

NOW = datetime(2026, 1, 1, tzinfo=UTC)
RECENT = datetime(2025, 12, 10, tzinfo=UTC)


def _job(job_id: str) -> dict[str, Any]:
    return {
        "id": job_id,
        "company_id": "co_a",
        "source_id": "src_1",
        "title": "Data Engineer",
        "posting_status": "active",
        "canonical_url": f"https://acme.test/jobs/{job_id}",
        "description_text": "python and sql",
        "content_hash": f"hash-{job_id}",
        "first_seen_at": NOW.isoformat(),
        "last_seen_at": NOW.isoformat(),
        "fetched_at": NOW.isoformat(),
    }


async def _seed(container) -> None:
    async with container.uow_factory() as uow:
        await uow.companies.upsert("co_a", "Acme")
        jobs = [_job(f"job_{index}") for index in range(3)]
        for job in jobs:
            container.uow_factory.jobs[str(job["id"])] = job
        service = KeywordPersistenceService(container)
        for job in jobs:
            await service.persist_for_job(uow, job, RECENT)
        await uow.commit()


async def _call(dispatcher, method: str, params: dict[str, Any]) -> dict[str, Any]:
    response = await dispatcher.handle_request(
        {"id": "req_1", "method": method, "params": params}
    )
    assert response["ok"] is True, response
    return response["result"]


@pytest.mark.asyncio
async def test_keyword_trends_round_trip() -> None:
    from app.worker.main import _build_dispatcher

    container = build_test_container()
    await _seed(container)
    dispatcher = _build_dispatcher(container)

    result = await _call(dispatcher, "keywords.trends", {"periods": 3, "limit": 5})

    assert result["scope"] == "global"
    assert result["bucket"] == "month"
    assert result["n_jobs"] == 3
    assert result["periods"]
    period = {row["period"]: row for row in result["periods"]}["2025-12"]
    assert period["insufficient"] is True
    assert all(row["prevalence"] is None for row in period["keywords"])


@pytest.mark.asyncio
async def test_keyword_trends_rejects_an_unknown_bucket() -> None:
    from app.worker.main import _build_dispatcher

    container = build_test_container()
    dispatcher = _build_dispatcher(container)

    response = await dispatcher.handle_request(
        {"id": "req_2", "method": "keywords.trends", "params": {"bucket": "day"}}
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "validation.invalid_params"


@pytest.mark.asyncio
async def test_weight_suggestion_round_trip_and_acceptance() -> None:
    from app.worker.main import _build_dispatcher

    container = build_test_container()
    await _seed(container)
    dispatcher = _build_dispatcher(container)

    proposals = await _call(dispatcher, "matching.get_weight_proposals", {})
    assert proposals["insufficient_evidence"] is True
    assert proposals["proposal"] is None

    applied = await _call(
        dispatcher,
        "matching.apply_weight_proposal",
        {"weights": {"skills_resume": 0.5}, "reason": "accepted from the screen"},
    )
    assert applied["ok"] is True
    assert applied["source"] == "manual"
    assert applied["version_id"]

    history = await _call(dispatcher, "matching.get_weight_history", {})
    assert len(history["versions"]) == 1
    assert history["versions"][0]["id"] == applied["version_id"]


@pytest.mark.asyncio
async def test_applying_invalid_weights_is_an_error_envelope() -> None:
    from app.worker.main import _build_dispatcher

    container = build_test_container()
    dispatcher = _build_dispatcher(container)

    response = await dispatcher.handle_request(
        {
            "id": "req_3",
            "method": "matching.apply_weight_proposal",
            "params": {"weights": {"skills_resume": "not-a-number"}},
        }
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "validation.invalid_params"
