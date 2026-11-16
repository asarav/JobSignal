"""Task list / cancel use cases."""

from __future__ import annotations

from app.application.container import Container
from app.application.dto import CancelTaskParams, TaskFilter
from app.domain.models.tasks import BackgroundTask


class TasksUseCase:
    def __init__(self, container: Container) -> None:
        self._container = container

    async def list_tasks(self, filter: TaskFilter) -> list[BackgroundTask]:
        async with self._container.uow_factory() as uow:
            return list(await uow.tasks.list(status=filter.status, limit=filter.limit))

    async def cancel_task(self, params: CancelTaskParams) -> None:
        """Cooperative cancellation; unknown or finished tasks are no-op successes."""
        runner = self._container.task_runner
        if runner is not None and await runner.cancel(params.task_id):
            return

        async with self._container.uow_factory() as uow:
            task = await uow.tasks.get(params.task_id)
            if task is None:
                return
            # A task left running by a previous process (or already terminal)
            # cannot be cooperatively cancelled; leave it untouched.
            if task.status.value in {"queued", "running"}:
                from dataclasses import replace

                from app.domain.models.enums import TaskStatus

                updated = replace(
                    task,
                    status=TaskStatus.CANCELLED,
                    finished_at=self._container.clock.now(),
                )
                await uow.tasks.save(updated)
                await uow.commit()
                self._container.events.publish(
                    "task.cancelled", {"task_id": task.id}, task_id=task.id
                )
