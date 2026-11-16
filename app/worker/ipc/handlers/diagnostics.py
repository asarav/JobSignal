"""Diagnostics handlers (sandboxed fetch + long-running sleep task)."""

from __future__ import annotations

from app.application.commands.diagnostics import FetchUrlUseCase
from app.application.dto import FetchUrlParams, SleepTaskParams
from app.application.orchestration.task_runner import TaskRunner
from app.domain.models.common import PortError


class DiagnosticsHandlers:
    def __init__(self, fetch_url: FetchUrlUseCase, task_runner: TaskRunner | None) -> None:
        self._fetch_url = fetch_url
        self._task_runner = task_runner

    async def fetch_url(self, params: FetchUrlParams) -> dict:
        return (await self._fetch_url.execute(params)).model_dump()

    async def sleep(self, params: SleepTaskParams) -> dict:
        if self._task_runner is None:
            raise PortError(
                code="plugin.unavailable", message="task runner is not configured"
            )
        task = await self._task_runner.submit("diagnostics.sleep", params.model_dump())
        return {"task_id": task.id}
