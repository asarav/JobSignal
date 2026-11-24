"""jobs.* handlers."""

from __future__ import annotations

from app.application.commands.jobs import JobsUseCase
from app.application.dto import (
    EmptyParams,
    JobFilter,
    JobIdParams,
    JobMergeParams,
    JobSearchParams,
    RankedJobParams,
)


class JobsHandlers:
    def __init__(self, jobs: JobsUseCase, runner: object | None = None) -> None:
        self._jobs = jobs
        self._runner = runner

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

    async def ranked(self, params: RankedJobParams) -> dict:
        return (await self._jobs.ranked(params)).model_dump()

    async def list_saved(self, params: EmptyParams) -> dict:
        return (await self._jobs.list_saved()).model_dump()

    async def get_detail(self, params: JobIdParams) -> dict:
        return (await self._jobs.get_detail(params.job_id)).model_dump()

    async def verify_now(self, params: JobIdParams) -> dict:
        if self._runner is not None:
            task = await self._runner.submit(
                "jobs.verify_now", {"job_id": params.job_id}
            )
            return {
                "task_id": task.id,
                "checked": 0,
                "states": {},
                "job_ids": [params.job_id],
            }
        result = await self._jobs.verify_now(params.job_id)
        return {"task_id": None, **result}
