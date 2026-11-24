"""The benchmark harness measures every hot path the M11 plan names."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.application.benchmarks import (
    BASELINE_SLACK_MS,
    BUDGETS_MS,
    NAMED_HOT_PATHS,
    SCENARIOS,
    baseline_limit_ms,
    run_scenarios,
    summarize,
)
from app.application.keywords import KeywordPersistenceService
from tests.support.containers import build_test_container

#: Independent copy of the plan's list. If someone deletes a scenario from both
#: the harness and its own tuple, this literal still fails the suite.
PLAN_HOT_PATHS = (
    "jobs.list",
    "jobs.ranked",
    "applications.get_analytics",
    "keywords.prevalence",
    "keywords.trends",
)


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "company_id": "co_a",
        "source_id": "src_1",
        "title": "Data Engineer",
        "posting_status": "active",
        "canonical_url": f"https://acme.test/jobs/{job_id}",
        "description_text": "python and sql",
        "content_hash": f"hash-{job_id}",
        "first_seen_at": "2026-01-01T00:00:00Z",
        "posted_at": "2026-01-01T00:00:00Z",
        "fetched_at": "2026-01-01T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_every_hot_path_is_measured_within_its_budget() -> None:
    container = build_test_container()
    async with container.uow_factory() as uow:
        await uow.companies.upsert("co_a", "Acme")
        for index in range(20):
            container.uow_factory.jobs[f"job_{index}"] = _job(f"job_{index}")
        await uow.commit()
    persistence = KeywordPersistenceService(container)
    async with container.uow_factory() as uow:
        for index in range(20):
            await persistence.persist_for_job(
                uow, container.uow_factory.jobs[f"job_{index}"], container.clock.now()
            )
        await uow.commit()

    results = await run_scenarios(container, repeats=1)
    summary = summarize(results)

    assert [item.scenario for item in results] == list(SCENARIOS)
    assert set(BUDGETS_MS) == set(SCENARIOS)
    assert all(item.median_ms >= 0 for item in results)
    assert isinstance(summary["pass"], bool)
    assert set(summary["scenarios"]) == set(SCENARIOS)


def test_the_plan_named_paths_are_all_measured_and_baselined() -> None:
    assert set(NAMED_HOT_PATHS) == set(PLAN_HOT_PATHS)
    assert set(PLAN_HOT_PATHS) <= set(SCENARIOS)
    assert set(PLAN_HOT_PATHS) <= set(BUDGETS_MS)

    path = Path(__file__).resolve().parents[2] / "benchmarks" / "hot_paths_baseline.json"
    baseline = json.loads(path.read_text(encoding="utf-8"))
    assert baseline["tolerance"] == 1.25
    assert baseline["absolute_slack_ms"] == BASELINE_SLACK_MS
    assert set(baseline["scenarios"]) == set(SCENARIOS)
    for name in PLAN_HOT_PATHS:
        assert baseline["scenarios"][name]["median_ms"] > 0
    for entry in baseline["scenarios"].values():
        assert entry["median_ms"] > 0


def test_the_noise_floor_cannot_hide_a_real_regression() -> None:
    # A 6 ms read may drift a few ms, but a 4x slowdown must still fail.
    assert baseline_limit_ms(6.2) == pytest.approx(11.2)
    assert 25.0 > baseline_limit_ms(6.2)
    # The relative rule dominates once the read is large enough.
    assert baseline_limit_ms(40.0) == pytest.approx(50.0)
