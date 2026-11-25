"""Power-user cockpit contracts: application workspace and apply queue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.application.commands.applications import ApplicationsUseCase
from app.application.commands.jobs import JobsUseCase
from app.application.dto import ApplicationListParams, JobsListSavedParams
from app.domain.models.application import Application
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from tests.support.containers import build_test_container
from tests.support.fakes import FakeClock

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _application(index: int, *, status: str, applied_at: datetime | None) -> Application:
    return Application(
        id=f"app_{index}",
        company_id="co_acme",
        job_id=f"job_{index}",
        job_title=f"Data Engineer {index}",
        job_title_family="engineering",
        job_canonical_url=f"https://example.test/jobs/{index}",
        job_description_text="python sql pipelines",
        job_department="Data",
        job_locations=({"city": "Berlin"},),
        job_workplace_type="remote",
        job_snapshot_id=None,
        status=status,
        applied_at=applied_at,
        resume_version_id=None,
        notes="",
        created_at=NOW - timedelta(days=index),
        updated_at=NOW - timedelta(days=index),
    )


@pytest.mark.asyncio
async def test_application_workspace_pages_filters_and_names_companies():
    clock = FakeClock(NOW)
    factory = InMemoryUnitOfWorkFactory()
    factory.companies["co_acme"] = {"id": "co_acme", "canonical_name": "Acme"}
    for index in range(1, 31):
        factory.jobs[f"job_{index}"] = {
            "id": f"job_{index}",
            "company_id": "co_acme",
            "title": f"Data Engineer {index}",
            "description_text": "python sql pipelines",
            "workplace_type": "remote",
            "posting_status": "active",
            "first_seen_at": NOW - timedelta(days=index),
        }
    container = build_test_container(uow_factory=factory, clock=clock)

    async with factory() as uow:
        for index in range(1, 31):
            applied_at = NOW - timedelta(days=30) if index <= 2 else NOW
            await uow.applications.add(
                _application(index, status="applied", applied_at=applied_at)
            )
        await uow.applications.add(
            _application(31, status="interviewing", applied_at=NOW - timedelta(days=3))
        )
        await uow.applications.add(
            _application(32, status="offered", applied_at=NOW - timedelta(days=4))
        )
        await uow.commit()

    use_case = ApplicationsUseCase(container)
    page = await use_case.list(ApplicationListParams(limit=25))
    assert page.total == 32
    assert len(page.applications) == 25
    assert page.summary.total == 32
    assert page.summary.applied == 32
    assert page.summary.follow_up == 2
    assert page.summary.interviewing == 1
    assert page.summary.offers == 1
    assert page.applications[0].company_name == "Acme"

    waiting = await use_case.list(ApplicationListParams(view="follow_up", limit=10))
    assert waiting.total == 2
    assert {row.status for row in waiting.applications} == {"applied"}

    searched = await use_case.list(
        ApplicationListParams(query="Acme", sort="oldest", limit=5)
    )
    assert searched.total == 32
    assert len(searched.applications) == 5
    assert searched.applications[0].job_title == "Data Engineer 1"


@pytest.mark.asyncio
async def test_saved_queue_hides_tracked_roles_and_pages():
    clock = FakeClock(NOW)
    factory = InMemoryUnitOfWorkFactory()
    factory.companies["co_acme"] = {"id": "co_acme", "canonical_name": "Acme"}
    container = build_test_container(uow_factory=factory, clock=clock)
    for index in range(1, 8):
        factory.jobs[f"job_{index}"] = {
            "id": f"job_{index}",
            "company_id": "co_acme",
            "title": f"Saved Role {index}",
            "description_text": "python sql",
            "workplace_type": "remote",
            "posting_status": "active",
            "first_seen_at": NOW - timedelta(days=index),
        }
        factory.job_user_state[f"job_{index}"] = {
            "job_id": f"job_{index}",
            "state": "saved",
            "note": "",
            "updated_at": NOW.isoformat(),
        }

    async with factory() as uow:
        await uow.applications.add(_application(1, status="applied", applied_at=NOW))
        await uow.commit()

    use_case = JobsUseCase(container)
    ready = await use_case.list_saved(JobsListSavedParams(view="ready", limit=3))
    assert ready.total == 6
    assert len(ready.jobs) == 3
    assert all(not job.tracked for job in ready.jobs)

    all_saved = await use_case.list_saved(JobsListSavedParams(view="all", limit=20))
    assert all_saved.total == 7
    tracked = {job.job_id: job.tracked for job in all_saved.jobs}
    assert tracked["job_1"] is True
    assert tracked["job_2"] is False

    searched = await use_case.list_saved(
        JobsListSavedParams(view="all", query="Saved Role 4", limit=20)
    )
    assert searched.total == 1
    assert searched.jobs[0].title == "Saved Role 4"
