"""The Profile screen's Recompute path, over the real IPC dispatcher.

The bug was an empty ``job_ids`` list from the UI being read as an explicit
empty target set.  These tests send exactly that payload (and the payload the
screen now sends) through the registered handlers against a real SQLite
database, then read the ranked shortlist back over IPC.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
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
from tests.support.containers import build_test_container

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "m5"
NOW = datetime.now(UTC)


async def _container(tmp_path: Path) -> Any:
    db = tmp_path / "ipc.sqlite"
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
    return build_test_container(sqlite_db=db, data_dir=str(tmp_path / "data"))


def _job(
    job_id: str,
    title: str,
    *,
    required: list[str],
    description: str,
    seniority: Seniority,
    age_days: int,
) -> NormalizedJob:
    return NormalizedJob(
        company_ref=CompanyRef(name="Northwind"),
        title=title,
        normalized_title=title.lower(),
        title_family="engineering",
        description_text=description,
        description_html=None,
        locations=[LocationRef(raw="Berlin, Germany", city="Berlin")],
        workplace_type=WorkplaceType.ONSITE,
        employment_type=EmploymentType.FULL_TIME,
        salary=None,
        posted_at=NOW - timedelta(days=age_days),
        source_id="s1",
        source_posting_id=job_id,
        canonical_url=f"https://example.com/{job_id}",
        original_urls=[],
        department=None,
        seniority=seniority,
        required_skills=required,
        preferred_skills=[],
        extraction_status=ExtractionStatus.COMPLETE,
        extraction_confidence=1.0,
        field_confidence={},
        provenance=[],
        posting_status_hint=PostingStatus.ACTIVE,
        fetched_at=NOW,
    )


async def _seed_jobs(container: Any) -> None:
    specs = [
        (
            "job_backend",
            "Senior Backend Engineer",
            ["python", "sql", "aws", "docker"],
            "Python services on AWS with SQL, PostgreSQL and Docker.",
            Seniority.SENIOR,
            1,
        ),
        (
            "job_data",
            "Data Engineer",
            ["python", "sql"],
            "Python and SQL reporting pipelines.",
            Seniority.MID,
            2,
        ),
        (
            "job_design",
            "Product Designer",
            ["figma"],
            "Figma design systems and user research.",
            Seniority.MID,
            3,
        ),
    ]
    async with container.uow_factory() as uow:
        for job_id, title, required, description, seniority, age in specs:
            await uow.jobs.upsert(
                job_id,
                company_id="co",
                source_id="s1",
                job=_job(
                    job_id,
                    title,
                    required=required,
                    description=description,
                    seniority=seniority,
                    age_days=age,
                ),
                content_hash=f"hash-{job_id}",
            )
        await uow.commit()


async def _call(dispatcher: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
    response = await dispatcher.handle_request(
        {"id": f"req-{method}", "method": method, "params": params}
    )
    assert response["ok"] is True, response
    result = response["result"]
    assert isinstance(result, dict)
    return result


async def _import_resume(dispatcher: Any) -> None:
    await _call(
        dispatcher,
        "resume.import",
        {"file_path": str(FIXTURES / "golden_resume.txt"), "label": "proof"},
    )


@pytest.mark.asyncio
async def test_recompute_with_empty_job_ids_scores_every_collected_job(
    tmp_path: Path,
) -> None:
    from app.worker.main import _build_dispatcher

    container = await _container(tmp_path)
    await _seed_jobs(container)
    dispatcher = _build_dispatcher(container)
    await _import_resume(dispatcher)

    # Exactly what ResumeRoute.tsx used to send; the empty list must mean
    # "everything collected", never "score nothing".
    run = await _call(
        dispatcher, "matching.run", {"profile_id": "default", "job_ids": []}
    )
    assert run["available_jobs"] == 3
    assert run["considered"] == 3
    assert run["scored"] == 3
    assert run["reused"] == 0
    assert run["truncated"] is False
    assert len(run["results"]) == 3
    scores = {row["job_id"]: row["fit_score"] for row in run["results"]}
    assert set(scores) == {"job_backend", "job_data", "job_design"}
    assert all(isinstance(score, float) and score > 0 for score in scores.values())

    ranked = await _call(
        dispatcher, "jobs.ranked", {"sort": "rank", "window": 50, "ensure_fit": False}
    )
    job_ids = [row["job_id"] for row in ranked["jobs"]]
    assert job_ids[0] == "job_backend"
    assert ranked["jobs"][0]["fit_score"] is not None
    assert ranked["jobs"][0]["rank_score"] is not None
    assert ranked["jobs"][0]["fit_score"] > ranked["jobs"][-1]["fit_score"]
    ranked_scores = [row["rank_score"] for row in ranked["jobs"]]
    assert ranked_scores == sorted(ranked_scores, reverse=True)

    # The payload the screen sends today (no job_ids field at all).
    omitted = await _call(dispatcher, "matching.run", {"profile_id": "default"})
    assert omitted["considered"] == 3
    assert omitted["scored"] == 0
    assert omitted["reused"] == 3

    # Per-job runs, used by jobs.ranked, still target only those jobs.
    targeted = await _call(
        dispatcher,
        "matching.run",
        {"profile_id": "default", "job_ids": ["job_data"]},
    )
    assert targeted["available_jobs"] == 1
    assert targeted["considered"] == 1
    assert targeted["results"][0]["job_id"] == "job_data"


@pytest.mark.asyncio
async def test_recompute_reports_an_empty_corpus_without_error(tmp_path: Path) -> None:
    from app.worker.main import _build_dispatcher

    dispatcher = _build_dispatcher(await _container(tmp_path))
    await _import_resume(dispatcher)

    run = await _call(
        dispatcher, "matching.run", {"profile_id": "default", "job_ids": []}
    )
    assert run["results"] == []
    assert run["available_jobs"] == 0
    assert run["considered"] == 0
    assert run["truncated"] is False
