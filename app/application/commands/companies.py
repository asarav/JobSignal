"""companies.* read models and merge decisions (M9).

Company intelligence is descriptive: observed postings, reposts, your own
applications and a hiring summary at the sample tier the data supports. Merged
companies resolve to their survivor at read time; the companies table is never
rewritten, so an unmerge restores the previous view exactly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from app.application.company_identity import CompanyIdentityService
from app.application.dto import (
    CompanyDetailResult,
    CompanyInfo,
    CompanyListResult,
    CompanyMergeHistoryResult,
    CompanyMergeParams,
    CompanyMergeResult,
    JobSummary,
)
from app.domain.models.common import PortError
from app.domain.services.company_resolution import canonical_map


class CompaniesUseCase:
    def __init__(self, container: Any) -> None:
        self._container = container
        self._identity = CompanyIdentityService(container)

    async def list(self) -> CompanyListResult:
        async with self._container.uow_factory() as uow:
            companies = list(await uow.companies.list())
            rows, _ = await uow.jobs.filtered_page({}, limit=2000)
            applications = list(await uow.applications.list(limit=1000))
            merged = canonical_map(await uow.company_merges.latest_actions())
            await uow.commit()

        jobs_by_company: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            canonical = _canonical(merged, row.get("company_id"))
            jobs_by_company.setdefault(canonical, []).append(dict(row))
        apps_by_company: dict[str, int] = {}
        for application in applications:
            canonical = merged.get(application.company_id, application.company_id)
            apps_by_company[canonical] = apps_by_company.get(canonical, 0) + 1
        children: dict[str, list[str]] = {}
        for merged_id, survivor in merged.items():
            children.setdefault(survivor, []).append(merged_id)

        summaries: list[CompanyInfo] = []
        for company in companies:
            company_id = str(company.get("id") or "")
            if company_id in merged:
                continue
            company_jobs = jobs_by_company.get(company_id, [])
            summaries.append(
                CompanyInfo(
                    company_id=company_id,
                    name=str(company.get("canonical_name") or company_id),
                    domain=_text(company.get("domain")),
                    job_count=len(company_jobs),
                    active_job_count=sum(
                        1
                        for job in company_jobs
                        if str(job.get("posting_status") or "active") == "active"
                    ),
                    application_count=apps_by_company.get(company_id, 0),
                    last_seen_at=_latest_seen(company_jobs),
                    merged_ids=sorted(children.get(company_id, [])),
                    merged_company_count=len(children.get(company_id, [])),
                )
            )
        summaries.sort(key=lambda item: (-item.job_count, item.name.lower()))
        return CompanyListResult(companies=summaries, total=len(summaries))

    async def get_detail(self, company_id: str) -> CompanyDetailResult:
        async with self._container.uow_factory() as uow:
            company = await uow.companies.get(company_id)
            if company is None:
                raise PortError(
                    code="not_found",
                    message=f"unknown company: {company_id}",
                    retryable=False,
                )
            merged = canonical_map(await uow.company_merges.latest_actions())
            survivor = merged.get(company_id, company_id)
            survivor_row = (
                company if survivor == company_id else await uow.companies.get(survivor)
            )
            if survivor_row is None:
                raise PortError(
                    code="not_found",
                    message=f"unknown company: {survivor}",
                    retryable=False,
                )
            children = sorted(
                [merged_id for merged_id, value in merged.items() if value == survivor]
            )
            rows, _ = await uow.jobs.filtered_page({}, limit=2000)
            company_jobs = [
                dict(row)
                for row in rows
                if _canonical(merged, row.get("company_id")) == survivor
            ]
            company_jobs.sort(
                key=lambda row: str(row.get("last_seen_at") or row.get("first_seen_at") or ""),
                reverse=True,
            )
            job_ids = [str(row["id"]) for row in company_jobs[:200]]
            assessments = await uow.intelligence.assessments.latest_for_jobs(job_ids)
            summary = await self._latest_summary(uow, [survivor, *children])
            applications = [
                row
                for row in await uow.applications.list(limit=1000)
                if merged.get(row.company_id, row.company_id) == survivor
            ]
            reposts = {
                job_id: len(await uow.dedup.list_repost_events(job_id))
                for job_id in job_ids
            }
            await uow.commit()

        jobs: list[JobSummary] = []
        for row in company_jobs[:200]:
            assessment = assessments.get(str(row["id"])) or {}
            jobs.append(
                JobSummary(
                    job_id=str(row["id"]),
                    title=str(row.get("title") or ""),
                    company_id=survivor,
                    canonical_url=_text(row.get("canonical_url")),
                    posting_status=str(row.get("posting_status") or "active"),
                    source_id=_text(row.get("source_id")),
                    location_text=str(row.get("location_text") or ""),
                    workplace_type=str(row.get("workplace_type") or "unknown"),
                    posted_at=_text(row.get("posted_at")),
                    first_seen_at=_text(row.get("first_seen_at")),
                    risk=float(assessment["risk"]) if "risk" in assessment else None,
                    risk_confidence=(
                        float(assessment["confidence"]) if "confidence" in assessment else None
                    ),
                    risk_class=_text(assessment.get("class")),
                    credibility=(
                        1.0 - float(assessment["risk"]) if "risk" in assessment else None
                    ),
                    reason=(
                        f"{reposts.get(str(row['id']), 0)} reposts observed"
                        if reposts.get(str(row["id"]), 0)
                        else None
                    ),
                )
            )

        observed_reposts = sum(reposts.values())
        active = sum(1 for job in jobs if job.posting_status == "active")
        return CompanyDetailResult(
            company_id=survivor,
            requested_id=company_id if company_id != survivor else None,
            name=str(survivor_row.get("canonical_name") or survivor),
            domain=_text(survivor_row.get("domain")),
            jobs=jobs,
            job_count=len(jobs),
            active_job_count=active,
            repost_count=observed_reposts,
            summary=summary,
            merged_ids=children,
            applications=[
                {
                    "application_id": application.id,
                    "job_id": application.job_id,
                    "status": application.status,
                    "applied_at": _iso(application.applied_at),
                }
                for application in applications
            ],
            recent_window_days=90,
            window_start=_iso(self._container.clock.now() - timedelta(days=90)),
        )

    async def merge(
        self, params: CompanyMergeParams
    ) -> CompanyMergeResult:
        payload = await self._identity.merge(
            params.company_id, params.into_id, params.reason
        )
        return CompanyMergeResult(**payload)

    async def unmerge(self, company_id: str, reason: str = "") -> CompanyMergeResult:
        payload = await self._identity.unmerge(company_id, reason)
        return CompanyMergeResult(**payload)

    async def merge_history(
        self, company_id: str | None = None, *, limit: int = 200
    ) -> CompanyMergeHistoryResult:
        payload = await self._identity.history(company_id, limit=limit)
        return CompanyMergeHistoryResult(**payload)

    async def _latest_summary(
        self, uow: Any, company_ids: Sequence[str]
    ) -> dict[str, Any] | None:
        rows = await uow.intelligence.company_summaries.latest_for_companies(
            list(company_ids)
        )
        if not rows:
            return None
        best = max(
            rows.values(), key=lambda row: str(row.get("computed_at") or "")
        )
        return dict(best)


def _canonical(merged: Mapping[str, str], value: object) -> str:
    company_id = str(value or "")
    return str(merged.get(company_id, company_id))


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _latest_seen(rows: list[dict[str, Any]]) -> str | None:
    values = [
        str(row.get("last_seen_at") or row.get("first_seen_at") or "") for row in rows
    ]
    values = [value for value in values if value]
    return max(values) if values else None


__all__ = ["CompaniesUseCase"]
