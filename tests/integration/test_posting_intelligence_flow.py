"""End-to-end posting-intelligence flow over the in-memory unit of work.

The point of these tests is the parts the pure engine tests cannot see:
history assembly, reuse of identical inputs, classification transitions, and
reproducibility from stored rows alone (spec §9.6, §19.7).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from app.application.intelligence import (
    CompanySummaryService,
    PostingIntelligenceUseCase,
    RiskEvaluationService,
    signal_from_row,
)
from app.domain.services.risk import HeuristicRiskModel
from tests.support.containers import build_test_container

CLOSED = (
    "This position has been filled. We are no longer accepting applications "
    "for this role."
)
OPEN = (
    "We build a data platform. You will own ingestion pipelines, mentor two "
    "engineers, and improve reliability."
)


def _job(container, job_id: str, *, description: str, age_days: int = 120) -> dict:
    now = container.clock.now()
    first_seen = now - timedelta(days=age_days)
    return {
        "id": job_id,
        "company_id": "co_1",
        "source_id": "src_1",
        "title": "Data Engineer",
        "description_text": description,
        "posting_status": "active",
        "content_hash": f"hash-{job_id}-{len(description)}",
        "first_seen_at": first_seen.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_seen_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _seed(container, description: str = CLOSED) -> dict:
    factory = container.uow_factory
    factory.companies["co_1"] = {
        "id": "co_1",
        "canonical_name": "Acme",
        "normalized_name": "acme",
    }
    job = _job(container, "job_1", description=description)
    factory.jobs["job_1"] = job
    return job


@pytest.mark.asyncio
async def test_evaluation_records_signals_assessment_and_evidence() -> None:
    container = build_test_container()
    _seed(container)
    result = await RiskEvaluationService(container).evaluate(job_ids=["job_1"])

    assert result["evaluated"] == 1
    use_case = PostingIntelligenceUseCase(container)
    payload = await use_case.get_signals("job_1")

    assert payload["assessment"] is not None
    assert payload["assessment"]["class"] == "likely_inactive"
    assert payload["assessment"]["confidence"] >= 0.70
    assert len(payload["signals"]) == 8
    closure = next(
        row
        for row in payload["signals"]
        if row["signal_id"] == "signal.explicit_closure_language"
    )
    assert closure["strong"] is True
    assert closure["evidence"]


@pytest.mark.asyncio
async def test_unchanged_inputs_are_reused_not_duplicated() -> None:
    container = build_test_container()
    _seed(container)
    service = RiskEvaluationService(container)

    first = await service.evaluate(job_ids=["job_1"])
    second = await service.evaluate(job_ids=["job_1"])

    assert first["evaluated"] == 1
    assert second["evaluated"] == 0
    assert second["skipped"] == 1

    history = await PostingIntelligenceUseCase(container).get_risk_history("job_1")
    assert len(history["assessments"]) == 1


@pytest.mark.asyncio
async def test_new_content_appends_an_assessment_and_records_the_class_change() -> None:
    container = build_test_container()
    job = _seed(container)
    service = RiskEvaluationService(container)
    await service.evaluate(job_ids=["job_1"])

    job["description_text"] = OPEN
    job["content_hash"] = "hash-updated"
    factory = container.uow_factory
    factory.reposts.append(
        {
            "job_id": "job_1",
            "class": "reposted_identical",
            "detected_at": container.clock.now(),
            "confidence": 0.9,
        }
    )

    second = await service.evaluate(job_ids=["job_1"])
    assert second["evaluated"] == 1

    history = await PostingIntelligenceUseCase(container).get_risk_history("job_1")
    assert len(history["assessments"]) == 2
    assert history["changes"], "a changed class must be recorded"
    assert history["changes"][0]["from_class"] == "likely_inactive"
    assert history["changes"][0]["to_class"] != "likely_inactive"


@pytest.mark.asyncio
async def test_assessments_reproduce_from_stored_signals() -> None:
    container = build_test_container()
    _seed(container)
    service = RiskEvaluationService(container)
    await service.evaluate(job_ids=["job_1"])

    payload = await PostingIntelligenceUseCase(container).get_signals("job_1")
    stored = payload["assessment"]
    config = await service.current_config()

    recomputed = HeuristicRiskModel().assess(
        [signal_from_row(row) for row in payload["signals"]],
        config,
        container.clock.now(),
    )

    assert recomputed.risk == pytest.approx(stored["risk"])
    assert recomputed.confidence == pytest.approx(stored["confidence"])
    assert recomputed.risk_class.value == stored["class"]
    assert recomputed.config_hash == stored.get("config_hash", recomputed.config_hash)


@pytest.mark.asyncio
async def test_company_summaries_respect_the_sample_tier_gate() -> None:
    container = build_test_container()
    factory = container.uow_factory
    factory.companies["co_1"] = {
        "id": "co_1",
        "canonical_name": "Acme",
        "normalized_name": "acme",
    }
    now = container.clock.now()
    for index in range(9):
        job_id = f"job_{index}"
        job = _job(container, job_id, description=OPEN, age_days=90 + index)
        factory.jobs[job_id] = job
        if index < 3:
            factory.reposts.append(
                {
                    "job_id": job_id,
                    "class": "reposted_identical",
                    "detected_at": now - timedelta(days=5),
                    "confidence": 0.9,
                }
            )

    service = CompanySummaryService(container)
    written = await service.compute(["co_1"])
    assert written == 1

    summary = await PostingIntelligenceUseCase(container).get_company_assessment(
        "co_1"
    )
    assert summary["summary"]["sample_tier"] == "adequate"
    labels = {item["code"] for item in summary["summary"]["labels"]}
    assert "repeated_reposting" in labels


@pytest.mark.asyncio
async def test_company_summaries_say_insufficient_instead_of_labelling() -> None:
    container = build_test_container()
    factory = container.uow_factory
    factory.companies["co_1"] = {
        "id": "co_1",
        "canonical_name": "Acme",
        "normalized_name": "acme",
    }
    factory.jobs["job_1"] = _job(container, "job_1", description=OPEN, age_days=30)

    await CompanySummaryService(container).compute(["co_1"])
    summary = await PostingIntelligenceUseCase(container).get_company_assessment(
        "co_1"
    )

    assert summary["summary"]["sample_tier"] == "insufficient"
    assert summary["summary"]["labels"] == []


@pytest.mark.asyncio
async def test_configure_model_appends_a_version_and_reassesses() -> None:
    container = build_test_container()
    _seed(container)
    use_case = PostingIntelligenceUseCase(container)
    service = RiskEvaluationService(container)
    await service.evaluate(job_ids=["job_1"])

    configured = await use_case.configure_model({"weights": {"posting_age": 0.25}})

    assert configured["config"]["weights"]["posting_age"] == pytest.approx(0.25)
    current = await service.current_config()
    assert current.weight_for("signal.posting_age") == pytest.approx(0.25)

    result = await service.evaluate(job_ids=["job_1"])
    assert result["evaluated"] == 1, "a new model config is a new assessment"


@pytest.mark.asyncio
async def test_configure_model_rejects_unknown_keys() -> None:
    from app.domain.models.common import PortError

    container = build_test_container()
    with pytest.raises(PortError):
        await PostingIntelligenceUseCase(container).configure_model(
            {"mystery": True}
        )
