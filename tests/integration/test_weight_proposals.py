"""Outcome associations produce a suggestion; only acceptance changes weights."""

from __future__ import annotations

import pytest
from app.application.commands.applications import ApplicationsUseCase
from app.application.commands.matching import MatchingUseCase
from app.application.dto import ApplicationCreateParams, ApplicationOutcomeParams
from app.application.weight_proposals import WeightProposalService
from app.domain.services.matching import DEFAULT_WEIGHTS
from tests.support.containers import build_test_container

DIMENSIONS = (
    "skills_resume",
    "experience_seniority",
    "responsibilities",
    "location_workplace",
    "compensation",
    "freshness",
)


async def _seed(container, *, responded: int, other: int) -> None:
    job_ids = [f"job_{index}" for index in range(responded + other)]
    async with container.uow_factory() as uow:
        await uow.companies.upsert("co_a", "Acme")
        for job_id in job_ids:
            container.uow_factory.jobs[job_id] = {
                "id": job_id,
                "company_id": "co_a",
                "source_id": "src_1",
                "title": "Data Engineer",
                "posting_status": "active",
                "canonical_url": f"https://acme.test/jobs/{job_id}",
                "first_seen_at": "2026-01-01T00:00:00Z",
                "posted_at": "2026-01-01T00:00:00Z",
                "fetched_at": "2026-01-01T00:00:00Z",
            }
        await uow.commit()

    uses = ApplicationsUseCase(container)
    applications = []
    for index, job_id in enumerate(job_ids):
        created = await uses.create(ApplicationCreateParams(job_id=job_id))
        applications.append(created.application_id)
        high = index < responded
        dimensions = [
            {
                "dimension": dimension,
                "strategy_id": f"match.{dimension}",
                "strategy_version": "1.0.0",
                "score": (
                    0.9 if dimension == "skills_resume" and high else
                    0.4 if dimension == "skills_resume" else 0.5
                ),
                "available": True,
                "weight": 1 / len(DIMENSIONS),
                "details": {},
            }
            for dimension in DIMENSIONS
        ]
        async with container.uow_factory() as uow:
            await uow.matches.save_result(
                f"mr_{index}",
                job_id,
                "default",
                None,
                "fit.weighted_v1",
                "1.0.0",
                {dimension: 1 / len(DIMENSIONS) for dimension in DIMENSIONS},
                f"hash_{index}",
                0.5,
                1.0,
                dimensions,
                "2026-01-01T00:00:00Z",
            )
            await uow.commit()
    for application_id in applications[:responded]:
        await uses.record_outcome(
            ApplicationOutcomeParams(application_id=application_id, kind="interview")
        )


@pytest.mark.asyncio
async def test_a_suggestion_names_its_sample_and_does_not_apply_itself() -> None:
    container = build_test_container()
    await _seed(container, responded=12, other=12)
    service = WeightProposalService(container)

    payload = await service.proposals()

    assert payload["applications"] == 24
    assert payload["responded"] == 12
    assert payload["insufficient_evidence"] is False
    by_dimension = {row["dimension"]: row for row in payload["associations"]}
    assert by_dimension["skills_resume"]["gap"] == pytest.approx(0.5)
    assert by_dimension["compensation"]["gap"] == pytest.approx(0.0)
    proposal = payload["proposal"]
    assert proposal is not None
    assert proposal["dimension"] == "skills_resume"
    assert proposal["sample_size"] == 24
    assert proposal["requires_confirmation"] is True
    assert proposal["weights"]["skills_resume"] > payload["weights"]["skills_resume"]
    assert sum(proposal["weights"].values()) == pytest.approx(1.0)

    assert payload["weights"] == pytest.approx(DEFAULT_WEIGHTS)
    assert payload["latest_version"] is None, (
        "reading a suggestion never writes a version"
    )


@pytest.mark.asyncio
async def test_a_small_sample_is_presented_as_insufficient() -> None:
    container = build_test_container()
    await _seed(container, responded=3, other=3)

    payload = await WeightProposalService(container).proposals()

    assert payload["insufficient_evidence"] is True
    assert payload["proposal"] is None
    assert all(row["insufficient"] for row in payload["associations"])


@pytest.mark.asyncio
async def test_accepting_appends_a_version_and_keeps_the_previous_one() -> None:
    container = build_test_container()
    await _seed(container, responded=12, other=12)
    service = WeightProposalService(container)
    payload = await service.proposals()
    proposal = payload["proposal"]
    assert proposal is not None

    first = await service.apply(
        weights=proposal["weights"],
        evidence={"sample_size": proposal["sample_size"], "gap": proposal["gap"]},
    )
    second = await service.apply(
        weights={"skills_resume": 0.5, "freshness": 0.5}, source="manual"
    )
    history = await service.history()

    assert first["previous_weights"] is None
    assert second["previous_weights"] == first["weights"]
    assert [row["id"] for row in history["versions"]] == [
        second["version_id"],
        first["version_id"],
    ]
    assert history["versions"][0]["previous_weights"] == first["weights"]
    assert history["versions"][1]["source"] == "proposal"
    assert history["weights"] == second["weights"]


@pytest.mark.asyncio
async def test_manual_weight_edits_are_recorded_too() -> None:
    container = build_test_container()
    await _seed(container, responded=1, other=1)

    await MatchingUseCase(container).update_weights("default", {"skills_resume": 0.6})
    history = await WeightProposalService(container).history()

    assert len(history["versions"]) == 1
    assert history["versions"][0]["source"] == "manual"
    assert history["versions"][0]["weights"]["skills_resume"] == pytest.approx(0.48)


@pytest.mark.asyncio
async def test_an_application_without_a_stored_fit_is_not_counted() -> None:
    container = build_test_container()
    await _seed(container, responded=2, other=2)
    async with container.uow_factory() as uow:
        container.uow_factory.jobs["job_extra"] = {
            "id": "job_extra",
            "company_id": "co_a",
            "source_id": "src_1",
            "title": "Analyst",
            "first_seen_at": "2026-01-01T00:00:00Z",
        }
        await uow.commit()
    await ApplicationsUseCase(container).create(
        ApplicationCreateParams(job_id="job_extra")
    )

    payload = await WeightProposalService(container).proposals()

    assert payload["applications"] == 4, "a missing fit is excluded, not a zero"
