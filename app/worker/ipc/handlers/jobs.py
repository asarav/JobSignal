"""jobs.* handlers."""

from __future__ import annotations

from app.application.commands.jobs import JobsUseCase
from app.application.dto import JobFilter, JobSearchParams


class JobsHandlers:
    def __init__(self, jobs: JobsUseCase) -> None:
        self._jobs = jobs

    async def list(self, params: JobFilter) -> dict:
        return (await self._jobs.list(params)).model_dump()

    async def search(self, params: JobSearchParams) -> dict:
        return (await self._jobs.search(params)).model_dump()
