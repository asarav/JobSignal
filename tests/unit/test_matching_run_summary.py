"""An empty job list means "everything collected", and the run says what it did."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
from app.application.commands.matching import MatchingUseCase
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


async def _seed(tmp_path: Path, count: int = 3) -> SimpleNamespace:
    db = tmp_path / "matching.sqlite"
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
        # Matching results reference the candidate profile.
        await uow.resumes.add_profile("default", "Default")
        for index in range(count):
            job = NormalizedJob(
                company_ref=CompanyRef(name="Northwind"),
                title=f"Backend Engineer {index + 1}",
                normalized_title="backend engineer",
                title_family="backend_engineer",
                description_text="Python and SQL pipelines",
                description_html=None,
                locations=[LocationRef(raw="Berlin, Germany", city="Berlin")],
                workplace_type=WorkplaceType.ONSITE,
                employment_type=EmploymentType.FULL_TIME,
                salary=None,
                posted_at=datetime.now(UTC) - timedelta(days=1),
                source_id="s1",
                source_posting_id=f"p{index}",
                canonical_url=f"https://example.com/jobs/{index}",
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
                f"job_{index}",
                company_id="co",
                source_id="s1",
                job=job,
                content_hash=f"h_{index}",
            )
        await uow.commit()
    return SimpleNamespace(
        uow_factory=factory, clock=SimpleNamespace(now=datetime.now)
    )


async def test_empty_job_ids_scores_the_whole_corpus_and_reports_counts(
    tmp_path: Path,
) -> None:
    container = await _seed(tmp_path)
    matching = MatchingUseCase(container)  # type: ignore[arg-type]

    first, summary = await matching.run_with_summary("default", [])
    assert len(first) == 3
    assert summary == {
        "available_jobs": 3,
        "considered": 3,
        "scored": 3,
        "reused": 0,
        "truncated": False,
    }

    # The same inputs reuse the stored result instead of rescoring silently.
    second, summary = await matching.run_with_summary("default")
    assert len(second) == 3
    assert summary["scored"] == 0
    assert summary["reused"] == 3


async def test_explicit_job_ids_still_target_only_those_jobs(tmp_path: Path) -> None:
    container = await _seed(tmp_path)
    matching = MatchingUseCase(container)  # type: ignore[arg-type]

    results, summary = await matching.run_with_summary("default", ["job_1"])
    assert [row["job_id"] for row in results] == ["job_1"]
    assert summary["considered"] == 1
    assert summary["available_jobs"] == 1
    assert summary["truncated"] is False


async def test_an_empty_corpus_reports_zero_rather_than_failing(tmp_path: Path) -> None:
    container = await _seed(tmp_path, count=0)
    matching = MatchingUseCase(container)  # type: ignore[arg-type]

    results, summary = await matching.run_with_summary("default", [])
    assert results == []
    assert summary["available_jobs"] == 0
    assert summary["considered"] == 0


def test_extensive_experience_scores_above_have_experience() -> None:
    from app.domain.models.matching import MatchContext
    from app.domain.services.matching import SkillsResumeStrategy

    strategy = SkillsResumeStrategy()
    job = {"required_skills": ["python"], "preferred_skills": [], "description_text": "python sql"}
    ctx = MatchContext(now=datetime.now(UTC))

    def score(status: str) -> float:
        profile = SimpleNamespace(
            skills=[],
            skill_evidence={},
            skill_status={"python": status},
            resume_text="python sql",
        )
        dimension = strategy.score(profile, job, ctx)
        assert dimension.score is not None
        return dimension.score

    assert score("extensive_experience") > score("have_experience")
    assert score("have_experience") > score("learning")
    assert score("learning") > score("no_experience")
