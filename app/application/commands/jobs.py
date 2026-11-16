"""Job listing/search use cases (M1)."""

from typing import cast

from app.application.container import Container
from app.application.dto import JobFilter, JobListResult, JobSearchParams, JobSummary


class JobsUseCase:
    def __init__(self, container: Container) -> None:
        self._container = container

    async def list(self, params: JobFilter) -> JobListResult:
        async with self._container.uow_factory() as uow:
            rows = await uow.jobs.list(
                source_id=params.source_id,
                company_id=params.company_id,
                limit=params.limit,
                offset=params.offset,
            )
            total = await uow.jobs.count()
            await uow.commit()
        return JobListResult(
            jobs=[
                JobSummary(
                    job_id=str(r.get("id")),
                    title=str(r.get("title") or ""),
                    company_id=cast(str, r.get("company_id")) if r.get("company_id") is not None else None,
                    canonical_url=cast(str, r.get("canonical_url")) if r.get("canonical_url") is not None else None,
                    posting_status=str(r.get("posting_status") or "active"),
                    source_id=cast(str, r.get("source_id")) if r.get("source_id") is not None else None,
                )
                for r in rows
            ],
            total=total,
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
                    company_id=cast(str, r.get("company_id")) if r.get("company_id") is not None else None,
                    canonical_url=cast(str, r.get("canonical_url")) if r.get("canonical_url") is not None else None,
                    posting_status=str(r.get("posting_status") or "active"),
                    source_id=cast(str, r.get("source_id")) if r.get("source_id") is not None else None,
                )
                for r in rows
            ],
            total=len(rows),
        )
