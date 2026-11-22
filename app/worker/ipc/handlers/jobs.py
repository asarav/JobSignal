"""jobs.* handlers."""

from __future__ import annotations

from app.application.commands.jobs import JobsUseCase
from app.application.dto import (
    EmptyParams,
    JobFilter,
    JobIdParams,
    JobMergeParams,
    JobSearchParams,
)


class JobsHandlers:
    def __init__(self, jobs: JobsUseCase) -> None:
        self._jobs = jobs

    async def list(self, params: JobFilter) -> dict:
        return (await self._jobs.list(params)).model_dump()

    async def search(self, params: JobSearchParams) -> dict:
        return (await self._jobs.search(params)).model_dump()

    async def merge(self, params: JobMergeParams) -> dict:
        await self._jobs.merge(params.job_id, params.related_job_id)
        return {"ok": True}

    async def unmerge(self, params: JobMergeParams) -> dict:
        await self._jobs.unmerge(params.job_id, params.related_job_id)
        return {"ok": True}

    async def list_duplicates(self, params: EmptyParams) -> dict:
        return (await self._jobs.list_duplicates()).model_dump()

    async def list_seen(self, params: EmptyParams) -> dict:
        return (await self._jobs.list_seen()).model_dump()

    async def get_detail(self, params: JobIdParams) -> dict:
        return (await self._jobs.get_detail(params.job_id)).model_dump()
