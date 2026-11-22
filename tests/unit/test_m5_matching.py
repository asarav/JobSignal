"""Unit tests for M5 matching persistence."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import aiosqlite
from app.application.commands.matching import MatchingUseCase
from app.application.commands.resume import ResumeUseCase
from app.domain.models.company import CompanyRef
from app.domain.models.enums import (
    EmploymentType,
    ExtractionStatus,
    PostingStatus,
    Seniority,
    WorkplaceType,
)
from app.domain.models.job import NormalizedJob
from app.domain.services.matching import DEFAULT_WEIGHTS
from app.infrastructure.persistence.sqlite.migrator import migrate
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory

RESUME = """Jane Doe

Skills
Python, SQL

Experience
Beta Inc
Engineer Jan 2018 - Jan 2021
- Wrote Python and SQL.
"""


async def test_matching_stores_effective_weights_and_dimensions(tmp_path) -> None:
    db = tmp_path / "matching.sqlite"
    await migrate(db)
    seed = await aiosqlite.connect(db)
    await seed.execute(
        "INSERT INTO companies(id, canonical_name, normalized_name, created_at) "
        "VALUES ('co','Co','co','2026-01-01T00:00:00Z')"
    )
    await seed.execute(
        "INSERT INTO sources(id, url, kind, created_at, updated_at) "
        "VALUES ('s1','https://example.com','ats','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"
    )
    await seed.commit()
    await seed.close()

    factory = SqliteUnitOfWorkFactory(db_path=db, clock_now=datetime.now)
    container = SimpleNamespace(uow_factory=factory, clock=SimpleNamespace(now=datetime.now))
    resume = ResumeUseCase(container, tmp_path / "data")  # type: ignore[arg-type]
    path = tmp_path / "resume.txt"
    path.write_text(RESUME, encoding="utf-8")
    await resume.import_resume(str(path), "v1")

    async with factory() as uow:
        job = NormalizedJob(
            company_ref=CompanyRef(name="Co"),
            title="Backend Engineer",
            normalized_title="backend engineer",
            title_family="backend_engineer",
            description_text="Python and SQL required.",
            description_html=None,
            locations=[],
            workplace_type=WorkplaceType.ONSITE,
            employment_type=EmploymentType.FULL_TIME,
            salary=None,
            posted_at=None,
            source_id="s1",
            source_posting_id="1",
            canonical_url="https://example.com/jobs/1",
            original_urls=[],
            department=None,
            seniority=Seniority.MID,
            required_skills=["python", "sql"],
            preferred_skills=[],
            extraction_status=ExtractionStatus.COMPLETE,
            extraction_confidence=1.0,
            field_confidence={},
            provenance=[],
            posting_status_hint=PostingStatus.ACTIVE,
            fetched_at=datetime.now(UTC),
        )
        await uow.jobs.upsert("job1", company_id="co", source_id="s1", job=job, content_hash="h")
        await uow.commit()

    use_case = MatchingUseCase(container)  # type: ignore[arg-type]
    results = await use_case.run("default")
    assert len(results) == 1

    conn = await aiosqlite.connect(db)
    try:
        cur = await conn.execute("SELECT weights_json FROM matching_results")
        row = await cur.fetchone()
        assert row is not None
        # Weights are recorded with the result, not left empty.
        assert json.loads(row[0]) == DEFAULT_WEIGHTS

        cur = await conn.execute("SELECT COUNT(*) FROM matching_dimension_scores")
        count = (await cur.fetchone())[0]
        assert count == len(DEFAULT_WEIGHTS)

        cur = await conn.execute("SELECT COUNT(*) FROM matching_explanations")
        assert (await cur.fetchone())[0] > 0
    finally:
        await conn.close()

    # The same inputs reuse the stored row instead of appending a duplicate.
    second = await use_case.run("default")
    assert second[0]["reused"] is True
    stored = await use_case.get_results("default")
    assert stored["weights"] == DEFAULT_WEIGHTS
    assert len(stored["results"]) == 1
    assert stored["results"][0]["job_title"] == "Backend Engineer"
    assert len(stored["results"][0]["dimensions"]) == len(DEFAULT_WEIGHTS)
    assert stored["results"][0]["explanations"]

    # Changing weights normalizes, persists and invalidates the stored result.
    weights = await use_case.update_weights("default", {"skills_resume": 2.0})
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    third = await use_case.run("default")
    assert third[0]["reused"] is False
    stored = await use_case.get_results("default")
    assert stored["results"][0]["weights"] == weights

    # A user skill status participates in the input hash and the score.
    async with factory() as uow:
        await uow.resumes.set_skill_status("default", "python", "no_experience")
        await uow.commit()
        assert await uow.resumes.get_skill_status("default") == {"python": "no_experience"}
    fourth = await use_case.run("default")
    assert fourth[0]["reused"] is False


async def test_second_import_records_parent_version(tmp_path) -> None:
    db = tmp_path / "versions.sqlite"
    await migrate(db)
    factory = SqliteUnitOfWorkFactory(db_path=db, clock_now=datetime.now)
    container = SimpleNamespace(uow_factory=factory, clock=SimpleNamespace(now=datetime.now))
    resume = ResumeUseCase(container, tmp_path / "data")  # type: ignore[arg-type]
    path = tmp_path / "resume.txt"
    path.write_text(RESUME, encoding="utf-8")
    first = await resume.import_resume(str(path), "v1")
    second = await resume.import_resume(str(path), "v2")

    async with factory() as uow:
        versions = await uow.resumes.list_versions("default")
        await uow.commit()
    by_id = {str(row["id"]): row for row in versions}
    assert by_id[second["version_id"]]["parent_version_id"] == first["version_id"]


def test_location_compensation_and_freshness_dimensions() -> None:
    from app.domain.models.matching import MatchContext
    from app.domain.services.matching import (
        CompensationStrategy,
        FreshnessStrategy,
        LocationWorkplaceStrategy,
    )

    profile = SimpleNamespace(
        preferences={
            "workplace_types": ["remote"],
            "remote_ok": True,
            "locations": ["berlin"],
            "desired_salary_target": 100000,
            "desired_salary_min": 80000,
        }
    )
    job = {
        "workplace_type": "remote",
        "locations": [{"city": "Berlin", "country": "Germany"}],
        "salary": {"min": 90000, "max": 120000},
        "posted_at": "2026-11-14T00:00:00Z",
    }
    location = LocationWorkplaceStrategy().score(profile, job, MatchContext())
    assert location.available and location.score == 1.0
    compensation = CompensationStrategy().score(profile, job, MatchContext())
    assert compensation.available and compensation.score == 1.0
    freshness = FreshnessStrategy().score(
        profile, job, MatchContext(now=datetime(2026, 11, 21, tzinfo=UTC))
    )
    assert freshness.available and 0.5 < (freshness.score or 0) < 1.0
    assert FreshnessStrategy().score(profile, {}, MatchContext()).available is False
