"""A ranked list separates fit from credibility and honours the policy."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.commands.jobs import JobsUseCase
from app.application.dto import RankedJobParams
from app.domain.models.posting import Explanation, RiskAssessment, RiskAssessmentRecord, RiskClass
from tests.support.containers import build_test_container

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _job(job_id: str, title: str) -> dict[str, Any]:
    return {
        "id": job_id,
        "company_id": "co_1",
        "source_id": "src_1",
        "title": title,
        "posting_status": "active",
        "canonical_url": f"https://acme.test/jobs/{job_id}",
        "description_text": "Own ingestion.",
        "first_seen_at": NOW.isoformat(),
        "fetched_at": NOW.isoformat(),
    }


async def _seed(container) -> None:
    for job_id, title in (
        ("job_a", "Strong fit"),
        ("job_b", "Likely inactive"),
        ("job_c", "Unscored"),
    ):
        container.uow_factory.jobs[job_id] = _job(job_id, title)
    async with container.uow_factory() as uow:
        for job_id, fit in (("job_a", 0.9), ("job_b", 0.8)):
            await uow.matches.save_result(
                f"match_{job_id}",
                job_id,
                "default",
                "resume_1",
                "fit.weighted_v1",
                "1",
                {},
                f"hash_{job_id}",
                fit,
                0.8,
                [],
                NOW,
            )
        await uow.intelligence.assessments.add(
            RiskAssessmentRecord(
                id="risk_a",
                job_id="job_a",
                model_version_id="model_1",
                signal_observation_ids=(),
                input_hash="hash_a",
                assessment=RiskAssessment(
                    model_id="risk.heuristic_v1",
                    model_version="1",
                    risk=0.1,
                    confidence=0.8,
                    risk_class=RiskClass.LIKELY_ACTIVE,
                    explanation=Explanation(summary="Fresh posting.", reasons=()),
                    evaluated_at=NOW,
                ),
            )
        )
        await uow.intelligence.assessments.add(
            RiskAssessmentRecord(
                id="risk_b",
                job_id="job_b",
                model_version_id="model_1",
                signal_observation_ids=(),
                input_hash="hash_b",
                assessment=RiskAssessment(
                    model_id="risk.heuristic_v1",
                    model_version="1",
                    risk=0.9,
                    confidence=0.8,
                    risk_class=RiskClass.LIKELY_INACTIVE,
                    explanation=Explanation(summary="Closed wording.", reasons=()),
                    evaluated_at=NOW,
                ),
            )
        )
        await uow.commit()


@pytest.mark.asyncio
async def test_default_policy_deprioritises_without_hiding() -> None:
    container = build_test_container()
    await _seed(container)

    result = await JobsUseCase(container).ranked(RankedJobParams(limit=10))

    assert [job.job_id for job in result.jobs] == ["job_a", "job_b", "job_c"]
    assert result.total == 3
    assert result.hidden_by_policy == 0
    assert result.policy == "deprioritize"
    scored = result.jobs[1]
    assert scored.risk_class == "likely_inactive"
    assert scored.rank_score == pytest.approx(0.8 * (1 - 0.5 * 0.9))
    assert any("credibility policy" in reason for reason in scored.rank_reasons)


@pytest.mark.asyncio
async def test_hide_high_risk_removes_low_credibility_rows() -> None:
    container = build_test_container()
    await _seed(container)

    result = await JobsUseCase(container).ranked(
        RankedJobParams(limit=10, ranking_policy="hide_high_risk")
    )

    assert [job.job_id for job in result.jobs] == ["job_a", "job_c"]
    assert result.hidden_by_policy == 1
    assert result.jobs[0].rank_score == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_include_hidden_shows_the_row_marked_hidden() -> None:
    container = build_test_container()
    await _seed(container)

    result = await JobsUseCase(container).ranked(
        RankedJobParams(limit=10, ranking_policy="require_evidence", include_hidden=True)
    )

    by_id = {job.job_id: job for job in result.jobs}
    assert by_id["job_b"].hidden_by_policy is True
    assert by_id["job_a"].hidden_by_policy is False


@pytest.mark.asyncio
async def test_headline_counts_cover_the_whole_corpus_not_the_page() -> None:
    container = build_test_container()
    await _seed(container)
    for index in range(9):
        job_id = f"job_x{index}"
        container.uow_factory.jobs[job_id] = _job(job_id, f"Extra {index}")
    async with container.uow_factory() as uow:
        # A second risky job: the page below holds one row, so a page-derived
        # count could not report both.
        await uow.intelligence.assessments.add(
            RiskAssessmentRecord(
                id="risk_c",
                job_id="job_c",
                model_version_id="model_1",
                signal_observation_ids=(),
                input_hash="hash_c",
                assessment=RiskAssessment(
                    model_id="risk.heuristic_v1",
                    model_version="1",
                    risk=0.6,
                    confidence=0.4,
                    risk_class=RiskClass.NEEDS_VERIFICATION,
                    explanation=Explanation(summary="Needs a check.", reasons=()),
                    evaluated_at=NOW,
                ),
            )
        )
        await uow.commit()

    result = await JobsUseCase(container).ranked(
        RankedJobParams(
            limit=1,
            seen_since="2025-01-01T00:00:00Z",
        )
    )

    assert len(result.jobs) == 1
    assert result.total == 12
    # job_b (likely_inactive) plus job_c (needs_verification), regardless of
    # the single-row page.
    assert result.needs_attention == 2
    assert result.new_since == 12

    future = await JobsUseCase(container).ranked(
        RankedJobParams(limit=1, seen_since="2027-01-01T00:00:00Z")
    )
    assert future.new_since == 0

    first_visit = await JobsUseCase(container).ranked(RankedJobParams(limit=1))
    assert first_visit.new_since == 12


@pytest.mark.asyncio
async def test_the_window_is_reported() -> None:
    container = build_test_container()
    await _seed(container)

    result = await JobsUseCase(container).ranked(RankedJobParams(limit=1, window=2))

    assert len(result.jobs) == 1
    assert result.window == 2
    assert result.window_truncated is True
