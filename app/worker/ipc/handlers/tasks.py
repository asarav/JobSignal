"""tasks.list / tasks.cancel handlers."""

from __future__ import annotations

from app.application.commands.tasks import TasksUseCase
from app.application.dto import CancelTaskParams, TaskFilter, task_summary


class TasksHandlers:
    def __init__(self, tasks: TasksUseCase) -> None:
        self._tasks = tasks

    async def list(self, params: TaskFilter) -> dict:
        items = await self._tasks.list_tasks(params)
        return {"tasks": [task_summary(t).model_dump(mode="json") for t in items]}

    async def cancel(self, params: CancelTaskParams) -> dict:
        await self._tasks.cancel_task(params)
        return {"ok": True}
