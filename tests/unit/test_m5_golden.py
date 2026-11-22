"""M5 exit criterion: golden resume/job fixtures produce expected dimensions."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest
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
from app.domain.models.job import LocationRef, NormalizedJob, SalaryRange
from app.infrastructure.persistence.sqlite.migrator import migrate
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "m5"


async def test_golden_resume_and_jobs_produce_expected_dimensions(tmp_path: Path) -> None:
    db = tmp_path / "golden.sqlite"
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
    container = SimpleNamespace(
        uow_factory=factory, clock=SimpleNamespace(now=datetime.now)
    )
    resume = ResumeUseCase(container, tmp_path / "data")  # type: ignore[arg-type]
    imported = await resume.import_resume(str(FIXTURES / "golden_resume.txt"), "golden")
    assert imported["years_experience"] == pytest.approx(10.5, abs=0.2)
    await resume.update_overrides(
        "default",
        {
            "seniority": "senior",
            "preferences": {
                "locations": ["Berlin"],
                "workplace_types": ["remote"],
                "remote_ok": True,
                "desired_salary_min": 90000,
                "desired_salary_target": 110000,
            },
        },
    )

    specs = json.loads((FIXTURES / "golden_jobs.json").read_text(encoding="utf-8"))
    async with factory() as uow:
        for key, spec in specs.items():
            salary = SalaryRange(**spec["salary"]) if spec["salary"] else None
            days = spec["posted_days_ago"]
            job = NormalizedJob(
                company_ref=CompanyRef(name="Northwind"),
                title=spec["title"],
                normalized_title=spec["title"].lower(),
                title_family=spec["title"].lower().replace(" ", "_"),
                description_text=spec["description"],
                description_html=None,
                locations=[LocationRef(**location) for location in spec["locations"]],
                workplace_type=WorkplaceType(spec["workplace_type"]),
                employment_type=EmploymentType.FULL_TIME,
                salary=salary,
                posted_at=datetime.now(UTC) - timedelta(days=days) if days else None,
                source_id="s1",
                source_posting_id=key,
                canonical_url=f"https://example.com/jobs/{key}",
                original_urls=[],
                department=None,
                seniority=Seniority(spec["seniority"]),
                required_skills=spec["required_skills"],
                preferred_skills=spec["preferred_skills"],
                extraction_status=ExtractionStatus.COMPLETE,
                extraction_confidence=1.0,
                field_confidence={},
                provenance=[],
                posting_status_hint=PostingStatus.ACTIVE,
                fetched_at=datetime.now(UTC),
            )
            await uow.jobs.upsert(
                f"job_{key}",
                company_id="co",
                source_id="s1",
                job=job,
                content_hash=f"h_{key}",
            )
        await uow.commit()

    matching = MatchingUseCase(container)  # type: ignore[arg-type]
    await matching.run("default")
    stored = await matching.get_results("default")
    results = {str(row["job_title"]): row for row in stored["results"]}

    complete = results["Senior Backend Engineer"]
    complete_dims = {dim["dimension"]: dim for dim in complete["dimensions"]}
    assert set(complete_dims) == {
        "skills_resume",
        "experience_seniority",
        "responsibilities",
        "location_workplace",
        "compensation",
        "freshness",
    }
    assert complete["dimension_coverage"] == pytest.approx(1.0)
    assert complete["fit_score"] >= 0.6
    assert 0.65 <= complete_dims["skills_resume"]["score"] <= 1.0
    assert 0.85 <= complete_dims["experience_seniority"]["score"] <= 1.0
    assert complete_dims["responsibilities"]["score"] > 0.3
    assert complete_dims["location_workplace"]["score"] == pytest.approx(1.0)
    assert complete_dims["compensation"]["score"] == pytest.approx(1.0)
    # 14-day half-life: exactly 0.5 at two weeks, allowing for runtime skew.
    assert 0.4 <= complete_dims["freshness"]["score"] <= 0.6

    sparse = results["Operations Assistant"]
    sparse_dims = {dim["dimension"]: dim for dim in sparse["dimensions"]}
    # Missing dimensions are excluded, never scored zero (spec sec 11.3).
    assert sparse_dims["location_workplace"]["available"] is False
    assert sparse_dims["compensation"]["available"] is False
    assert sparse_dims["location_workplace"]["score"] is None
    assert sparse_dims["compensation"]["score"] is None
    # Freshness falls back to first_seen_at, so it stays available.
    assert sparse_dims["freshness"]["available"] is True
    assert sparse["dimension_coverage"] == pytest.approx(0.8)
    assert sparse["fit_score"] > 0.0
    assert any(
        explanation["reason_code"] == "dimension.unavailable"
        for explanation in sparse["explanations"]
    )
