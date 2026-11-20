"""Job listing/search use cases (M3)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from app.application.container import Container
from app.application.dto import JobFilter, JobListResult, JobSearchParams, JobSummary
from app.domain.services.hard_filters import apply_hard_filters


class JobsUseCase:
    def __init__(self, container: Container) -> None:
        self._container = container

    async def list(self, params: JobFilter) -> JobListResult:
        async with self._container.uow_factory() as uow:
            if params.query:
                rows = await uow.jobs.search(params.query, limit=params.limit)
            else:
                rows = await uow.jobs.list(
                    source_id=params.source_id,
                    company_id=params.company_id,
                    limit=params.limit,
                    offset=params.offset,
                )
            total = await uow.jobs.count()
            await uow.commit()

        if params.filters:
            rows = apply_hard_filters(rows, params.filters)

        return JobListResult(
            jobs=[
                JobSummary(
                    job_id=str(r.get("id")),
                    title=str(r.get("title") or ""),
                    company_id=cast(str, r.get("company_id"))
                    if r.get("company_id") is not None
                    else None,
                    canonical_url=cast(str, r.get("canonical_url"))
                    if r.get("canonical_url") is not None
                    else None,
                    posting_status=str(r.get("posting_status") or "active"),
                    source_id=cast(str, r.get("source_id"))
                    if r.get("source_id") is not None
                    else None,
                    provenance=_provenance_strategies(r),
                )
                for r in rows
            ],
            total=total if not params.filters else len(rows),
        )

    async def search(self, params: JobSearchParams) -> JobListResult:
        async with self._container.uow_factory() as uow:
            rows = await uow.jobs.search(params.query, limit=params.limit)
            await uow.commit()
        return JobListResult(
            jobs=[
                JobSummary(
                    job_id=str(r.get("id")),
                    title=str(r.get("title") or ""),
                    company_id=cast(str, r.get("company_id"))
                    if r.get("company_id") is not None
                    else None,
                    canonical_url=cast(str, r.get("canonical_url"))
                    if r.get("canonical_url") is not None
                    else None,
                    posting_status=str(r.get("posting_status") or "active"),
                    source_id=cast(str, r.get("source_id"))
                    if r.get("source_id") is not None
                    else None,
                    provenance=_provenance_strategies(r),
                )
                for r in rows
            ],
            total=len(rows),
        )

    async def merge(self, job_id: str, related_job_id: str) -> None:
        async with self._container.uow_factory() as uow:
            await uow.jobs.add_relation(job_id, related_job_id, "user_merged", created_by="user")
            await uow.commit()

    async def unmerge(self, job_id: str, related_job_id: str) -> None:
        async with self._container.uow_factory() as uow:
            await uow.jobs.add_relation(job_id, related_job_id, "user_unmerged", created_by="user")
            await uow.commit()

    async def list_duplicates(self) -> JobListResult:
        async with self._container.uow_factory() as uow:
            rows = await uow.jobs.list(limit=200)
            dup_ids = set()
            for r in rows:
                rels = await uow.jobs.list_relations(str(r.get("id")))
                if rels:
                    dup_ids.add(str(r.get("id")))
            await uow.commit()
        jobs = [r for r in rows if str(r.get("id")) in dup_ids]
        return JobListResult(
            jobs=[
                JobSummary(
                    job_id=str(r.get("id")),
                    title=str(r.get("title") or ""),
                    company_id=None,
                    canonical_url=None,
                    posting_status=str(r.get("posting_status") or "active"),
                    source_id=None,
                    provenance=[],
                )
                for r in jobs
            ],
            total=len(jobs),
        )


def _provenance_strategies(row: Mapping[str, Any]) -> list[str]:
    """The strategies that contributed to a job, in pipeline order."""
    raw = row.get("provenance")
    if isinstance(raw, str):
        try:
            import json

            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    seen: list[str] = []
    for entry in raw:
        if isinstance(entry, Mapping):
            strategy = entry.get("strategy_id")
            if isinstance(strategy, str) and strategy not in seen:
                seen.append(strategy)
    return seen
