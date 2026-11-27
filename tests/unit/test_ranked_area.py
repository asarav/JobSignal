"""Ranked results respect the profile area unless the user widens them."""
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


async def test_ranked_withholds_known_out_of_area_roles_by_default(tmp_path: Path) -> None:
    db = tmp_path / "ranked.sqlite"
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
                "preferences": {
                    "locations": ["Berlin"],
                    "remote_ok": False,
                    "relocation": False,
                }
            },
        )
        for job_id, raw, city in (
            ("job_berlin", "Berlin, Germany", "Berlin"),
            ("job_austin", "Austin, TX, United States", "Austin"),
        ):
            job = NormalizedJob(
                company_ref=CompanyRef(name="Northwind"),
                title=f"Backend Engineer {city}",
                normalized_title="backend engineer",
                title_family="backend_engineer",
                description_text="Python and SQL pipelines",
                description_html=None,
                locations=[LocationRef(raw=raw, city=city)],
                workplace_type=WorkplaceType.ONSITE,
                employment_type=EmploymentType.FULL_TIME,
                salary=None,
                posted_at=datetime.now(UTC),
                source_id="s1",
                source_posting_id=job_id,
                canonical_url=f"https://example.com/{job_id}",
                original_urls=[],
                department=None,
                seniority=Seniority.SENIOR,
                required_skills=["python"],
                preferred_skills=[],
                extraction_status=ExtractionStatus.COMPLETE,
                extraction_confidence=1.0,
                field_confidence={},
                provenance=[],
                posting_status_hint=PostingStatus.ACTIVE,
                fetched_at=datetime.now(UTC),
            )
            await uow.jobs.upsert(
                job_id,
                company_id="co",
                source_id="s1",
                job=job,
                content_hash=f"h_{job_id}",
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
    assert [row.job_id for row in narrow.jobs] == ["job_berlin"]
    assert narrow.outside_area == 1
    assert narrow.total == 1

    widened = await jobs.ranked(RankedJobParams(include_out_of_area=True))
    assert {row.job_id for row in widened.jobs} == {"job_berlin", "job_austin"}
    assert widened.outside_area == 0
