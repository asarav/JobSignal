"""Required skills and remote country restrictions are hard visibility rules."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
from app.application.commands.jobs import JobsUseCase
from app.application.dto import RankedJobParams
from app.domain.models.company import CompanyRef
from app.domain.models.enums import (
    EmploymentType,
    ExtractionStatus,
    PostingStatus,
    Seniority,
    WorkplaceType,
)
from app.domain.models.job import LocationRef, NormalizedJob
from app.infrastructure.persistence.sqlite.migrator import migrate
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory


async def _seed_job(
    uow: object,
    *,
    job_id: str,
    title: str,
    location: str,
    description: str,
) -> None:
    job = NormalizedJob(
        company_ref=CompanyRef(name="Northwind"),
        title=title,
        normalized_title=title.lower(),
        title_family="backend_engineer",
        description_text=description,
        description_html=None,
        locations=[LocationRef(raw=location, city=location)],
        workplace_type=WorkplaceType.REMOTE,
        employment_type=EmploymentType.FULL_TIME,
        salary=None,
        posted_at=datetime.now(UTC),
        source_id="s1",
        source_posting_id=job_id,
        canonical_url=f"https://example.com/{job_id}",
        original_urls=[],
        department=None,
        seniority=Seniority.SENIOR,
        required_skills=[],
        preferred_skills=[],
        extraction_status=ExtractionStatus.COMPLETE,
        extraction_confidence=1.0,
        field_confidence={},
        provenance=[],
        posting_status_hint=PostingStatus.ACTIVE,
        fetched_at=datetime.now(UTC),
    )
    await uow.jobs.upsert(  # type: ignore[attr-defined]
        job_id,
        company_id="co",
        source_id="s1",
        job=job,
        content_hash=f"h_{job_id}",
    )


async def test_remote_india_and_missing_required_skills_are_withheld(
    tmp_path: Path,
) -> None:
    db = tmp_path / "requirements.sqlite"
    await migrate(db)
    seed = await aiosqlite.connect(db)
    await seed.execute(
        "INSERT INTO companies(id, canonical_name, normalized_name, created_at) "
        "VALUES ('co','Northwind','northwind','2026-01-01T00:00:00Z')"
    )
    await seed.execute(
        "INSERT INTO sources(id, url, kind, created_at, updated_at) "
        "VALUES ('s1','https://example.com','ats','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"
    )
    await seed.commit()
    await seed.close()

    factory = SqliteUnitOfWorkFactory(db_path=db, clock_now=datetime.now)
    async with factory() as uow:
        await uow.resumes.add_profile("default", "Default")
        await uow.resumes.update_overrides(
            "default",
            {
                "skills_added": ["python"],
                "preferences": {
                    "locations": ["Chicago, IL"],
                    "home_location": "Chicago, IL",
                    "authorized_countries": ["United States"],
                    "remote_ok": True,
                    "relocation": False,
                },
            },
        )
        await _seed_job(
            uow,
            job_id="job_python_us",
            title="Python Engineer",
            location="Remote (US)",
            description="Requirements:\n- Python",
        )
        await _seed_job(
            uow,
            job_id="job_rust_us",
            title="Rust Engineer",
            location="Remote (US)",
            description="Requirements:\n- Rust",
        )
        await _seed_job(
            uow,
            job_id="job_python_india",
            title="Python Engineer India",
            location="Remote - India",
            description="Requirements:\n- Python",
        )
        await uow.commit()

    container = SimpleNamespace(
        uow_factory=factory,
        clock=SimpleNamespace(now=datetime.now),
        config=SimpleNamespace(
            ranking=SimpleNamespace(credibility_policy="deprioritize")
        ),
    )
    jobs = JobsUseCase(container)  # type: ignore[arg-type]

    narrow = await jobs.ranked(RankedJobParams())
    assert [row.job_id for row in narrow.jobs] == ["job_python_us"]
    assert narrow.outside_area == 1
    assert narrow.hidden_missing_skills == 1

    widened = await jobs.ranked(
        RankedJobParams(include_missing_skills=True, include_out_of_area=True)
    )
    assert {row.job_id for row in widened.jobs} == {
        "job_python_us",
        "job_rust_us",
        "job_python_india",
    }
    assert widened.outside_area == 0
    assert widened.hidden_missing_skills == 0
