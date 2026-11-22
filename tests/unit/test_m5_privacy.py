"""Privacy: nothing personal leaves the machine or reaches the logs (§19.5.7)."""
from __future__ import annotations

import io
from datetime import UTC
from types import SimpleNamespace

import pytest
from app.application.commands.applications import (
    ApplicationsUseCase,
    SuppressionsUseCase,
)
from app.application.commands.matching import MatchingUseCase
from app.application.commands.resume import ResumeUseCase
from app.application.config import AppConfig
from app.application.dto import ApplicationCreateParams
from app.infrastructure.logging import configure_logging
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from tests.support.fakes import FakeClock, FakeHttpClient, FakeIdGenerator

RESUME_TEXT = "Python developer with React experience."


@pytest.mark.asyncio
async def test_resume_matching_and_tracking_are_network_free_and_quiet(tmp_path):
    stream = io.StringIO()
    configure_logging(stream=stream)

    http = FakeHttpClient()
    http.block_socket = True
    factory = InMemoryUnitOfWorkFactory()
    container = SimpleNamespace(
        uow_factory=factory,
        clock=FakeClock(),
        http=http,
        ids=FakeIdGenerator(),
        config=AppConfig(),
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "resumes").mkdir()
    resume_uc = ResumeUseCase(container, data_dir)  # type: ignore[arg-type]
    matching_uc = MatchingUseCase(container)  # type: ignore[arg-type]

    rpath = tmp_path / "r.txt"
    rpath.write_text(RESUME_TEXT, encoding="utf-8")
    res = await resume_uc.import_resume(str(rpath), "v1")
    assert res["version_id"]

    async with factory() as uow:
        from datetime import datetime

        from app.domain.models.company import CompanyRef
        from app.domain.models.job import (
            EmploymentType,
            ExtractionStatus,
            LocationRef,
            NormalizedJob,
            PostingStatus,
            Seniority,
            WorkplaceType,
        )
        job = NormalizedJob(
            company_ref=CompanyRef(name="Co"),
            title="Backend Engineer",
            normalized_title="backend engineer",
            title_family="backend_engineer",
            description_text="Python required",
            description_html=None,
            locations=[LocationRef(raw="NYC", city="NYC", region=None, country=None)],
            workplace_type=WorkplaceType.ONSITE,
            employment_type=EmploymentType.FULL_TIME,
            seniority=Seniority.MID,
            salary=None,
            posted_at=None,
            source_id="s1",
            source_posting_id="1",
            canonical_url="https://example.com/jobs/1",
            original_urls=[],
            department=None,
            required_skills=["python"],
            preferred_skills=[],
            extraction_status=ExtractionStatus.COMPLETE,
            extraction_confidence=1.0,
            field_confidence={},
            provenance=[],
            posting_status_hint=PostingStatus.ACTIVE,
            fetched_at=datetime.now(UTC),
        )
        await uow.jobs.upsert("job1", company_id="co", source_id="s1", job=job, content_hash="h")
        await uow.companies.upsert("co", "Co", normalized_name="co")
        await uow.commit()

    results = await matching_uc.run("default")
    assert isinstance(results, list)

    # Application tracking is local too: it records what the user did.
    tracked = await ApplicationsUseCase(container).create(  # type: ignore[arg-type]
        ApplicationCreateParams(job_id="job1")
    )
    assert tracked.status == "applied"
    suppressions = await SuppressionsUseCase(container).list()  # type: ignore[arg-type]
    assert isinstance(suppressions.suppressions, list)

    assert http.sent == []
    logs = stream.getvalue()
    assert RESUME_TEXT not in logs
    assert "Python required" not in logs


def test_a_logged_pii_field_is_redacted():
    stream = io.StringIO()
    logger = configure_logging(stream=stream)

    logger.info(
        "resume.imported",
        resume_text=RESUME_TEXT,
        email="ada@example.com",
        phone="+44 20 0000 0000",
        note="reference ada@example.com",
    )
    logs = stream.getvalue()

    assert RESUME_TEXT not in logs
    assert "ada@example.com" not in logs
    assert "+44 20 0000 0000" not in logs
    assert "[redacted]" in logs
