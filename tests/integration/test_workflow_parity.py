"""End-to-end parity: the workflow's real IPC calls over a seeded SQLite DB.

The browser preview is not involved. This spawns ``app.worker.main`` exactly as
the desktop shell does, then issues the same methods and parameter shapes the
Today, Find jobs, Saved, job detail, Profile and Companies screens issue. Every
response is checked for the fields those screens read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.application.config import AppConfig
from app.domain.models.application import Application
from app.domain.models.company import CompanyRef
from app.domain.models.enums import (
    EmploymentType,
    ExtractionStatus,
    PostingStatus,
    Seniority,
    WorkplaceType,
)
from app.domain.models.job import LocationRef, NormalizedJob, SalaryRange
from app.domain.models.source import SourceConfig
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory
from app.worker.ipc.method_catalog import MILESTONE_METHODS
from tests.integration.test_worker_process import spawn_worker

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
RESULT_MODELS = {spec.method: spec.result for spec in MILESTONE_METHODS}


class ValidatedWorker:
    """Validate every successful workflow reply against its catalog DTO."""

    def __init__(self, worker: Any) -> None:
        self._worker = worker

    async def request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = await self._worker.request(method, params)
        if response.get("ok") is True:
            model = RESULT_MODELS.get(method)
            assert model is not None, f"{method} has no result model"
            model.model_validate(response.get("result"))
        return response

    async def close(self) -> None:
        await self._worker.close()


def _job(job_id: str, company_id: str, title: str) -> NormalizedJob:
    return NormalizedJob(
        company_ref=CompanyRef(name="Acme" if company_id == "co_a" else "Acme Inc"),
        title=title,
        normalized_title=title.lower(),
        title_family="data_engineer",
        description_text=(
            "Build Python and SQL data pipelines. Own ingestion, dbt models and "
            "warehouse quality."
        ),
        description_html=None,
        locations=[LocationRef(raw="Berlin, Germany", city="Berlin", country="DE")],
        workplace_type=WorkplaceType.REMOTE,
        employment_type=EmploymentType.FULL_TIME,
        salary=SalaryRange(
            min_amount=80000,
            max_amount=105000,
            currency="EUR",
            period="year",
            annualized_min=80000,
            annualized_max=105000,
            raw_text="EUR 80,000 - 105,000",
        ),
        posted_at=NOW - timedelta(days=2),
        source_id="src_1",
        source_posting_id=job_id,
        canonical_url=f"https://acme.test/jobs/{job_id}",
        original_urls=[],
        department="Data",
        seniority=Seniority.SENIOR,
        required_skills=["python", "sql"],
        preferred_skills=["dbt"],
        extraction_status=ExtractionStatus.COMPLETE,
        extraction_confidence=1.0,
        field_confidence={},
        provenance=[],
        posting_status_hint=PostingStatus.ACTIVE,
        fetched_at=NOW,
    )


async def _seed(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / AppConfig().paths.db_name
    factory = SqliteUnitOfWorkFactory(db_path=db_path, clock_now=lambda: NOW)
    await factory.initialize()
    async with factory() as uow:
        await uow.companies.upsert("co_a", "Acme", domain="acme.test")
        await uow.companies.upsert("co_b", "Acme Inc", domain="acme-inc.test")
        await uow.sources.add(
            SourceConfig(
                id="src_1",
                url="https://acme.test/careers",
                kind="ats",
                connector_id="ats.greenhouse",
                ats_tenant_key="acme",
            )
        )
        await uow.jobs.upsert(
            "job_1",
            company_id="co_a",
            source_id="src_1",
            job=_job("job_1", "co_a", "Senior Data Engineer"),
            content_hash="hash-1",
        )
        await uow.jobs.upsert(
            "job_2",
            company_id="co_b",
            source_id="src_1",
            job=_job("job_2", "co_b", "Analytics Engineer"),
            content_hash="hash-2",
        )
        await uow.crawl_runs.create(
            "run-old", "src_1", NOW - timedelta(days=7)
        )
        await uow.crawl_runs.finish(
            "run-old",
            status="completed",
            finished_at=NOW - timedelta(days=7) + timedelta(minutes=2),
            stats={"new": 1},
        )
        await uow.crawl_runs.create(
            "run-new", "src_1", NOW - timedelta(hours=2)
        )
        await uow.crawl_runs.finish(
            "run-new",
            status="completed",
            finished_at=NOW - timedelta(hours=2) + timedelta(minutes=1),
            stats={"new": 2},
        )
        await uow.commit()


def _result(response: dict[str, Any]) -> dict[str, Any]:
    assert response.get("ok") is True, response
    result = response.get("result")
    assert isinstance(result, dict), response
    return result


async def _seed_volume(data_dir: Path) -> None:
    """Enough rows that any silent 200-row cap would fail the assertions below."""
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / AppConfig().paths.db_name
    factory = SqliteUnitOfWorkFactory(db_path=db_path, clock_now=lambda: NOW)
    await factory.initialize()
    async with factory() as uow:
        await uow.companies.upsert("co_a", "Acme", domain="acme.test")
        await uow.sources.add(
            SourceConfig(id="src_1", url="https://acme.test/careers", kind="ats")
        )
        for index in range(1, 321):
            job_id = f"volume_job_{index}"
            await uow.jobs.upsert(
                job_id,
                company_id="co_a",
                source_id="src_1",
                job=_job(job_id, "co_a", f"Data Engineer {index}"),
                content_hash=f"hash-{index}",
            )
            if index <= 10 or 251 <= index <= 300:
                await uow.job_user_state.set(
                    job_id, "saved", note="", updated_at=NOW
                )
            if index > 250:
                continue
            status = "applied"
            if 221 <= index <= 230:
                status = "interviewing"
            elif 231 <= index <= 240:
                status = "offered"
            elif index > 240:
                status = "rejected"
            applied_at = NOW - timedelta(days=30) if index <= 20 else NOW
            await uow.applications.add(
                Application(
                    id=f"volume_app_{index}",
                    company_id="co_a",
                    job_id=job_id,
                    job_title=f"Data Engineer {index}",
                    job_title_family="data_engineer",
                    job_canonical_url=f"https://acme.test/jobs/{job_id}",
                    job_description_text="python sql",
                    job_department="Data",
                    job_locations=(),
                    job_workplace_type="remote",
                    job_snapshot_id=None,
                    status=status,
                    applied_at=applied_at,
                    resume_version_id=None,
                    notes="",
                    created_at=applied_at,
                    updated_at=applied_at,
                )
            )
        await uow.commit()


@pytest.mark.asyncio
async def test_workflow_calls_match_the_real_worker(tmp_path):
    data_dir = tmp_path / "data"
    await _seed(data_dir)
    resume_path = tmp_path / "resume.txt"
    resume_path.write_text(
        "Alex Doe\n"
        "alex@example.com | Berlin | Remote\n\n"
        "SUMMARY\n"
        "Senior data engineer with 8 years building Python and SQL pipelines.\n\n"
        "EXPERIENCE\n"
        "Acme\nSenior Data Engineer 2020 - Present\n"
        "- Built Python ingestion and dbt models for the warehouse.\n\n"
        "SKILLS\nPython, SQL, dbt, data modelling\n",
        encoding="utf-8",
    )

    worker = ValidatedWorker(await spawn_worker(data_dir))
    try:
        # Today / workflow state, including the bounded crawl-run request.
        runs = _result(await worker.request("crawl_runs.list", {"limit": 1}))
        assert len(runs["runs"]) == 1
        assert runs["runs"][0]["run_id"] == "run-new"
        sources = _result(await worker.request("sources.list"))
        assert len(sources["sources"]) == 1
        assert sources["sources"][0]["source_id"] == "src_1"
        assert _result(await worker.request("jobs.list_saved"))["total"] == 0
        assert _result(await worker.request("applications.list"))["total"] == 0
        empty_resume = _result(
            await worker.request("resume.list_versions", {"profile_id": "default"})
        )
        assert empty_resume["versions"] == []

        # Profile setup: import, active version and profile state.
        imported = _result(
            await worker.request(
                "resume.import", {"file_path": str(resume_path), "label": "v1"}
            )
        )
        assert imported["version_id"]
        assert "python" in imported["skills"]
        versions = _result(
            await worker.request("resume.list_versions", {"profile_id": "default"})
        )
        assert len(versions["versions"]) == 1
        # SQLite stores the flag as 0/1; the viewer normalizes with Boolean().
        assert bool(versions["versions"][0]["is_active"]) is True
        profile = _result(
            await worker.request("resume.get_profile", {"profile_id": "default"})
        )
        assert profile["profile"]["id"] == "default"
        assert profile["profile"]["active_resume_version_id"] == imported["version_id"]
        assert profile["profile"]["skills"]
        assert _result(
            await worker.request(
                "resume.set_active",
                {"profile_id": "default", "version_id": imported["version_id"]},
            )
        )["ok"] is True
        assert _result(
            await worker.request(
                "resume.update_overrides",
                {
                    "profile_id": "default",
                    "overrides": {"years_experience_total": 8},
                },
            )
        )["ok"] is True
        assert _result(
            await worker.request(
                "resume.set_skill_status",
                {
                    "profile_id": "default",
                    "keyword_id": "python",
                    "status": "have_experience",
                },
            )
        )["ok"] is True

        matching = _result(
            await worker.request(
                "matching.run", {"profile_id": "default", "job_ids": []}
            )
        )
        assert len(matching["results"]) == 2

        # Find jobs: ranked (with policy/sort/filters) and the unranked tab.
        ranked = _result(
            await worker.request(
                "jobs.ranked",
                {
                    "filters": {},
                    "ranking_policy": "deprioritize",
                    "sort": "rank",
                    "include_hidden": False,
                    "include_suppressed": False,
                    "limit": 100,
                },
            )
        )
        assert ranked["total"] == 2
        assert len(ranked["jobs"]) == 2
        first = ranked["jobs"][0]
        for key in ("job_id", "title", "company_name", "fit_score", "rank_reasons"):
            assert key in first
        assert first["fit_score"] is not None
        listed = _result(
            await worker.request("jobs.list", {"filters": {}, "limit": 100})
        )
        assert listed["total"] == 2

        profiles = _result(await worker.request("search_profiles.list"))
        assert profiles["profiles"] == []
        saved_profile = _result(
            await worker.request(
                "search_profiles.save",
                {
                    "name": "Remote data",
                    "hard_filters": {"workplace_types": ["remote"]},
                    "ranking_policy": "deprioritize",
                },
            )
        )
        assert saved_profile["profile_id"]
        assert len(_result(await worker.request("search_profiles.list"))["profiles"]) == 1
        _result(
            await worker.request(
                "search_profiles.delete", {"profile_id": saved_profile["profile_id"]}
            )
        )

        # Job detail and the save -> apply state machine.
        detail = _result(await worker.request("jobs.get_detail", {"job_id": "job_1"}))
        assert detail["job"]["id"] == "job_1"
        assert detail["job"]["description_text"]
        assert isinstance(detail["snapshots"], list)
        saved_state = _result(
            await worker.request(
                "jobs.set_user_state", {"job_id": "job_1", "state": "saved"}
            )
        )
        assert saved_state == {"job_id": "job_1", "state": "saved", "hidden": False}
        saved_jobs = _result(await worker.request("jobs.list_saved"))
        assert saved_jobs["total"] == 1
        assert saved_jobs["jobs"][0]["job_id"] == "job_1"
        assert saved_jobs["jobs"][0]["saved"] is True

        application = _result(
            await worker.request(
                "applications.create", {"job_id": "job_1", "status": "applied"}
            )
        )
        assert application["job_id"] == "job_1"
        assert application["job_title"] == "Senior Data Engineer"
        assert application["company_id"] == "co_a"
        assert application["status"] == "applied"
        applications = _result(await worker.request("applications.list"))
        assert applications["total"] == 1
        for key in ("application_id", "job_id", "job_title", "status", "events"):
            assert key in applications["applications"][0]

        # Track: status, outcome, analytics and suppression state.
        updated = _result(
            await worker.request(
                "applications.update_status",
                {
                    "application_id": application["application_id"],
                    "status": "interviewing",
                },
            )
        )
        assert updated["status"] == "interviewing"
        assert _result(
            await worker.request(
                "applications.record_outcome",
                {"application_id": application["application_id"], "kind": "interview"},
            )
        )["ok"] is True
        analytics = _result(
            await worker.request("applications.get_analytics", {"days": 365})
        )
        assert analytics["applications"] == 1
        assert analytics["window_days"] == 365
        assert isinstance(analytics["groups"], list)
        suppressions = _result(await worker.request("applications.get_suppressions"))
        assert isinstance(suppressions["suppressions"], list)
        policy = _result(
            await worker.request("applications.get_suppression_policy")
        )
        assert policy["policy"]
        updated_policy = _result(
            await worker.request(
                "applications.update_suppression_policy",
                {"policy": policy["policy"]},
            )
        )
        assert updated_policy["policy"]
        if suppressions["suppressions"]:
            suppressed = suppressions["suppressions"][0]
            assert _result(
                await worker.request(
                    "applications.restore_suppressed_job",
                    {
                        "job_id": suppressed["job_id"],
                        "application_id": suppressed["application_id"],
                    },
                )
            )["ok"] is True

        # Companies list/detail/merge history/merge/unmerge.
        companies = _result(await worker.request("companies.list"))
        assert companies["total"] == 2
        company = {row["company_id"]: row for row in companies["companies"]}
        assert company["co_a"]["job_count"] == 1
        assert company["co_a"]["application_count"] == 1
        company_detail = _result(
            await worker.request("companies.get_detail", {"company_id": "co_a"})
        )
        assert company_detail["name"] == "Acme"
        assert len(company_detail["jobs"]) == 1
        assert len(company_detail["applications"]) == 1
        no_history = _result(
            await worker.request("companies.get_merge_history", {})
        )
        assert no_history["records"] == []
        assert no_history["currently_merged"] == {}

        merged = _result(
            await worker.request(
                "companies.merge",
                {
                    "company_id": "co_b",
                    "into_id": "co_a",
                    "reason": "same employer",
                },
            )
        )
        assert merged["changed"] is True
        assert merged["survivor_id"] == "co_a"
        assert merged["record_id"]
        company_after = _result(await worker.request("companies.list"))
        assert company_after["total"] == 1
        survivor = company_after["companies"][0]
        assert survivor["company_id"] == "co_a"
        assert survivor["job_count"] == 2
        assert survivor["application_count"] == 1
        assert survivor["merged_ids"] == ["co_b"]

        history = _result(
            await worker.request(
                "companies.get_merge_history", {"company_id": "co_b"}
            )
        )
        assert history["total"] == 1
        record = history["records"][0]
        assert record["action"] == "merge"
        assert record["merged_id"] == "co_b"
        assert record["survivor_id"] == "co_a"
        assert record["merged_name"] == "Acme Inc"
        assert record["survivor_name"] == "Acme"
        assert history["currently_merged"] == {"co_b": "co_a"}
        merged_detail = _result(
            await worker.request("companies.get_detail", {"company_id": "co_b"})
        )
        assert merged_detail["company_id"] == "co_a"
        assert merged_detail["requested_id"] == "co_b"
        assert len(merged_detail["jobs"]) == 2

        unmerged = _result(
            await worker.request("companies.unmerge", {"company_id": "co_b"})
        )
        assert unmerged["changed"] is True
        assert unmerged["survivor_id"] == "co_b"
        after_unmerge = _result(await worker.request("companies.list"))
        assert after_unmerge["total"] == 2
        history_after = _result(
            await worker.request(
                "companies.get_merge_history", {"company_id": "co_b"}
            )
        )
        assert [row["action"] for row in history_after["records"]] == [
            "unmerge",
            "merge",
        ]
        assert history_after["currently_merged"] == {}
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_volume_contracts_are_paged_and_report_true_totals(tmp_path):
    data_dir = tmp_path / "volume-data"
    await _seed_volume(data_dir)
    worker = ValidatedWorker(await spawn_worker(data_dir))
    try:
        applications = _result(
            await worker.request("applications.list", {"limit": 25, "offset": 0})
        )
        assert applications["total"] == 250
        assert len(applications["applications"]) == 25
        assert applications["summary"]["total"] == 250
        assert applications["summary"]["applied"] == 250
        assert applications["summary"]["active"] == 220
        assert applications["summary"]["follow_up"] == 20
        assert applications["summary"]["interviewing"] == 10
        assert applications["summary"]["offers"] == 10
        assert applications["summary"]["closed"] == 10
        assert applications["applications"][0]["company_name"] == "Acme"

        second_page = _result(
            await worker.request(
                "applications.list", {"limit": 25, "offset": 25, "sort": "newest"}
            )
        )
        assert len(second_page["applications"]) == 25
        assert {
            row["application_id"] for row in applications["applications"]
        }.isdisjoint({row["application_id"] for row in second_page["applications"]})

        waiting = _result(
            await worker.request(
                "applications.list", {"view": "follow_up", "limit": 25}
            )
        )
        assert waiting["total"] == 20
        searched = _result(
            await worker.request(
                "applications.list", {"query": "Acme", "limit": 5}
            )
        )
        assert searched["total"] == 250

        ready = _result(
            await worker.request("jobs.list_saved", {"view": "ready", "limit": 25})
        )
        assert ready["total"] == 50
        assert len(ready["jobs"]) == 25
        all_saved = _result(
            await worker.request("jobs.list_saved", {"view": "all", "limit": 50})
        )
        assert all_saved["total"] == 60
        assert len(all_saved["jobs"]) == 50
        assert any(job["tracked"] for job in all_saved["jobs"])

        # New-match filtering goes through the same hard-filter path as the UI.
        before = (NOW - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        after = (NOW + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        new_jobs = _result(
            await worker.request(
                "jobs.list",
                {"filters": {"first_seen_after": before}, "limit": 25},
            )
        )
        assert new_jobs["total"] == 320
        no_new_jobs = _result(
            await worker.request(
                "jobs.list",
                {"filters": {"first_seen_after": after}, "limit": 25},
            )
        )
        assert no_new_jobs["total"] == 0
    finally:
        await worker.close()
