"""Analytics and calibration over a real in-memory unit of work."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.analytics import CalibrationService, OutcomeAnalyticsService
from app.application.commands.applications import ApplicationsUseCase
from app.application.dto import ApplicationCreateParams, ApplicationOutcomeParams
from app.domain.models.posting import (
    EndpointObservationRecord,
    Explanation,
    RiskAssessment,
    RiskAssessmentRecord,
    RiskClass,
)
from tests.support.containers import build_test_container

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _job(job_id: str, company_id: str = "co_1") -> dict[str, Any]:
    return {
        "id": job_id,
        "company_id": company_id,
        "source_id": "src_1",
        "title": "Data Engineer",
        "posting_status": "active",
        "canonical_url": f"https://acme.test/jobs/{job_id}",
        "description_text": "Own ingestion.",
        "first_seen_at": NOW.isoformat(),
        "posted_at": NOW.isoformat(),
        "fetched_at": NOW.isoformat(),
    }


@pytest.mark.asyncio
async def test_analytics_counts_a_confirmed_response() -> None:
    container = build_test_container()
    container.uow_factory.jobs["job_1"] = _job("job_1")
    use_case = ApplicationsUseCase(container)
    created = await use_case.create(ApplicationCreateParams(job_id="job_1"))
    await use_case.record_outcome(
        ApplicationOutcomeParams(
            application_id=created.application_id, kind="interview"
        )
    )

    payload = await OutcomeAnalyticsService(container).get_analytics(
        days=365, profile_id="default"
    )

    assert payload["applications"] == 1
    assert payload["responded"] == 1
    risk_groups = [row for row in payload["groups"] if row["group"] == "risk_class"]
    assert risk_groups
    assert all(row["insufficient"] for row in risk_groups)


@pytest.mark.asyncio
async def test_no_response_is_recorded_but_not_counted_as_a_response() -> None:
    container = build_test_container()
    container.uow_factory.jobs["job_1"] = _job("job_1")
    use_case = ApplicationsUseCase(container)
    created = await use_case.create(ApplicationCreateParams(job_id="job_1"))
    await use_case.record_outcome(
        ApplicationOutcomeParams(
            application_id=created.application_id, kind="no_response"
        )
    )

    payload = await OutcomeAnalyticsService(container).get_analytics()
    assert payload["responded"] == 0


@pytest.mark.asyncio
async def test_calibration_waits_for_enough_observations() -> None:
    container = build_test_container()
    async with container.uow_factory() as uow:
        for job_id in ("job_1", "job_2", "job_3"):
            container.uow_factory.jobs[job_id] = _job(job_id, company_id=f"co_{job_id}")
            await uow.intelligence.assessments.add(
                RiskAssessmentRecord(
                    id=f"risk_{job_id}",
                    job_id=job_id,
                    model_version_id="model_1",
                    signal_observation_ids=(),
                    input_hash=f"hash_{job_id}",
                    assessment=RiskAssessment(
                        model_id="risk.heuristic_v1",
                        model_version="1",
                        risk=0.85,
                        confidence=0.8,
                        risk_class=RiskClass.LIKELY_INACTIVE,
                        explanation=Explanation(summary="Closed wording.", reasons=()),
                        evaluated_at=NOW,
                    ),
                )
            )
            await uow.intelligence.endpoints.add(
                EndpointObservationRecord(
                    id=f"obs_{job_id}",
                    job_id=job_id,
                    observed_at=NOW,
                    state="closed_message",
                    http_status=200,
                    final_url=f"https://acme.test/jobs/{job_id}",
                    details={},
                )
            )
        await uow.commit()

    payload = await CalibrationService(container).get_calibration()

    assert payload["sample_size"] == 3
    assert payload["proposal"] is None
    assert payload["buckets"]
