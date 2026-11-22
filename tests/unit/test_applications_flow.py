"""Application tracking → suppression → expiry → reappearance (spec §19.4)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from app.application.commands.applications import ApplicationsUseCase, SuppressionsUseCase
from app.application.commands.jobs import JobsUseCase
from app.application.dto import (
    ApplicationCreateParams,
    ApplicationStatusParams,
    JobFilter,
    JobUserStateParams,
    RestoreSuppressedJobParams,
)
from app.domain.services.suppression import (
    REASON_EXACT_REPOST,
    REASON_SIMILAR_ROLE,
)
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from tests.support.containers import build_test_container
from tests.support.fakes import FakeClock

BACKEND = (
    "We are hiring a backend engineer to build distributed services in Python and Go. "
    "You will own reliability, APIs and data models for a growing platform team."
)
ACCOUNTING = (
    "Own the monthly close, reconcile ledgers and prepare statutory filings for the "
    "group. Qualified accountant with IFRS experience required."
)


def _job(job_id: str, title: str, family: str, description: str) -> dict[str, object]:
    return {
        "id": job_id,
        "company_id": "co_1",
        "title": title,
        "normalized_title": title.lower(),
        "title_family": family,
        "description_text": description,
        "locations": [{"city": "Berlin", "country": "DE"}],
        "workplace_type": "remote",
        "department": "Platform",
        "canonical_url": f"https://example.com/jobs/{job_id}",
        "posting_status": "active",
        "source_id": "src_1",
        "provenance": [],
    }


def _build():
    clock = FakeClock()
    factory = InMemoryUnitOfWorkFactory()
    factory.jobs.update(
        {
            "job_1": _job("job_1", "Backend Engineer", "engineering", BACKEND),
            "job_2": _job("job_2", "Backend Engineer", "engineering", BACKEND),
            "job_3": _job("job_3", "Accountant", "finance", ACCOUNTING),
        }
    )
    container = build_test_container(uow_factory=factory, clock=clock)
    return (
        clock,
        ApplicationsUseCase(container),
        SuppressionsUseCase(container),
        JobsUseCase(container),
    )


@pytest.mark.asyncio
async def test_saving_a_job_never_starts_a_cooldown():
    clock, applications, suppressions, jobs = _build()

    created = await applications.create(
        ApplicationCreateParams(job_id="job_1", status="saved")
    )
    assert created.applied_at is None
    await applications.create(ApplicationCreateParams(job_id="job_2", status="interested"))

    assert (await suppressions.list()).suppressions == []
    assert (await jobs.list(JobFilter())).total == 3


@pytest.mark.asyncio
async def test_applied_job_suppresses_exact_and_similar_roles_then_expires():
    clock, applications, suppressions, jobs = _build()

    created = await applications.create(
        ApplicationCreateParams(job_id="job_1", status="saved")
    )
    applied = await applications.update_status(
        ApplicationStatusParams(application_id=created.application_id, status="applied")
    )
    assert applied.applied_at is not None
    assert [event["kind"] for event in applied.events] == ["created", "status_changed"]

    report = await suppressions.list()
    reasons = {row.job_id: row.reason for row in report.suppressions}
    assert reasons == {
        "job_1": REASON_EXACT_REPOST,
        "job_2": REASON_SIMILAR_ROLE,
    }
    assert report.policy["similar_role_cooldown_days"] == 180

    listed = await jobs.list(JobFilter())
    assert [job.job_id for job in listed.jobs] == ["job_3"]

    seen = await jobs.list_seen()
    seen_reasons = {job.job_id: job.reason for job in seen.jobs}
    assert seen_reasons["job_1"] == "suppressed"
    assert seen_reasons["job_2"] == "suppressed"

    # Cooldown lapses: the similar role returns to the ordinary list, the exact
    # repost does not (it has no expiry while the chain exists).
    clock.advance(timedelta(days=200).total_seconds())
    report = await suppressions.list()
    states = {row.job_id: row.state for row in report.suppressions}
    assert states["job_2"] == "expired"
    assert states["job_1"] == "active"

    listed = await jobs.list(JobFilter())
    assert {job.job_id for job in listed.jobs} == {"job_2", "job_3"}


@pytest.mark.asyncio
async def test_restore_returns_a_suppressed_job_and_sticks():
    clock, applications, suppressions, jobs = _build()
    created = await applications.create(
        ApplicationCreateParams(job_id="job_1", status="applied")
    )

    await suppressions.restore(RestoreSuppressedJobParams(job_id="job_1"))
    listed = await jobs.list(JobFilter())
    assert "job_1" in {job.job_id for job in listed.jobs}

    # Recording another milestone for the same job must not re-suppress it.
    await applications.update_status(
        ApplicationStatusParams(application_id=created.application_id, status="interviewing")
    )
    listed = await jobs.list(JobFilter())
    assert "job_1" in {job.job_id for job in listed.jobs}
    restored = [
        row for row in (await suppressions.list()).suppressions if row.job_id == "job_1"
    ]
    assert [row.state for row in restored] == ["restored"]


@pytest.mark.asyncio
async def test_dismissed_jobs_leave_the_default_list_but_stay_visible():
    clock, applications, suppressions, jobs = _build()

    result = await suppressions.set_user_state(
        JobUserStateParams(job_id="job_3", state="dismissed")
    )
    assert result.hidden is True
    listed = await jobs.list(JobFilter())
    assert "job_3" not in {job.job_id for job in listed.jobs}
    seen = await jobs.list_seen()
    assert {job.job_id: job.reason for job in seen.jobs}["job_3"] == "dismissed"


@pytest.mark.asyncio
async def test_policy_change_releases_and_reapplies_suppressions():
    clock, applications, suppressions, jobs = _build()
    await applications.create(ApplicationCreateParams(job_id="job_1", status="applied"))

    from app.application.dto import SuppressionPolicyParams

    report = await suppressions.update_policy(
        SuppressionPolicyParams(policy={"similar_role_cooldown_days": 0})
    )
    assert report.policy["similar_role_cooldown_days"] == 0
    # The similar role is released; the exact repost has no cooldown to shorten.
    assert {job.job_id for job in (await jobs.list(JobFilter())).jobs} == {
        "job_2",
        "job_3",
    }


@pytest.mark.asyncio
async def test_outcome_and_note_events_are_recorded():
    clock, applications, suppressions, jobs = _build()
    created = await applications.create(
        ApplicationCreateParams(job_id="job_1", status="applied")
    )
    from app.application.dto import ApplicationEventParams, ApplicationOutcomeParams

    await applications.add_event(
        ApplicationEventParams(
            application_id=created.application_id, kind="note", note="recruiter called"
        )
    )
    await applications.record_outcome(
        ApplicationOutcomeParams(application_id=created.application_id, kind="interview")
    )

    stored = (await applications.list()).applications[0]
    assert "recruiter called" in [event["note"] for event in stored.events]
    assert [outcome["kind"] for outcome in stored.outcomes] == ["interview"]
