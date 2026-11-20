"""Privacy: resume import and matching must not touch the network."""
from __future__ import annotations

from datetime import UTC
from types import SimpleNamespace

import pytest
from app.application.commands.matching import MatchingUseCase
from app.application.commands.resume import ResumeUseCase
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from tests.support.fakes import FakeClock, FakeHttpClient


@pytest.mark.asyncio
async def test_resume_and_matching_are_network_free(tmp_path):
    http = FakeHttpClient()
    http.block_socket = True
    factory = InMemoryUnitOfWorkFactory()
    container = SimpleNamespace(uow_factory=factory, clock=FakeClock(), http=http)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "resumes").mkdir()
    resume_uc = ResumeUseCase(container, data_dir)  # type: ignore[arg-type]
    matching_uc = MatchingUseCase(container)  # type: ignore[arg-type]

    rpath = tmp_path / "r.txt"
    rpath.write_text("Python developer with React experience.", encoding="utf-8")
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
    assert http.sent == []
