"""Bounded task runner for M0 long-running scaffolding.

M0 intentionally supports a single task type (``diagnostics.sleep``). The
runner still persists lifecycle state to ``background_tasks`` and emits the
standard ``task.progress`` / ``task.completed`` / ``task.cancelled`` /
``task.failed`` events so cancellation and event plumbing are real, not stubs.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from app.domain.models.common import PortError
from app.domain.models.enums import TaskStatus
from app.domain.models.tasks import BackgroundTask

if TYPE_CHECKING:
    from app.application.container import Container


class TaskRunner:
    def __init__(self, container: Container) -> None:
        self._container = container
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._queued: dict[str, BackgroundTask] = {}
        self._running: dict[str, asyncio.Task[None]] = {}
        self._worker: asyncio.Task[None] | None = None

    @property
    def depth(self) -> int:
        return len(self._queued) + len(self._running)

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._worker_loop(), name="lodestar-task-runner")

    async def stop(self) -> None:
        for task in list(self._running.values()):
            task.cancel()
        if self._queue is not None:
            while not self._queue.empty():
                self._queue.get_nowait()
        self._queued.clear()
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        if self._running:
            await asyncio.gather(*self._running.values(), return_exceptions=True)

    async def submit(self, task_type: str, params: dict[str, Any]) -> BackgroundTask:
        task = BackgroundTask(
            id=self._container.ids.new_id(),
            type=task_type,
            status=TaskStatus.QUEUED,
            params=dict(params),
            created_at=self._container.clock.now(),
        )
        await self._save(task, insert=True)
        self._queued[task.id] = task
        await self._queue.put(task.id)
        self._container.events.publish(
            "task.queued", {"type": task.type}, task_id=task.id
        )
        return task

    async def cancel(self, task_id: str) -> bool:
        running = self._running.get(task_id)
        if running is not None and not running.done():
            running.cancel()
            return True
        queued = self._queued.pop(task_id, None)
        if queued is not None:
            cancelled = replace(
                queued,
                status=TaskStatus.CANCELLED,
                finished_at=self._container.clock.now(),
            )
            await self._save(cancelled, insert=False)
            self._container.events.publish(
                "task.cancelled", {"task_id": task_id}, task_id=task_id
            )
            return True
        return False

    async def _worker_loop(self) -> None:
        while True:
            task_id = await self._queue.get()
            task = self._queued.pop(task_id, None)
            if task is None:
                continue
            runner = asyncio.create_task(self._execute(task), name=f"task:{task_id}")
            self._running[task_id] = runner
            try:
                await runner
            except asyncio.CancelledError:
                if self._worker is not None and self._worker.cancelling():
                    raise
            finally:
                self._running.pop(task_id, None)

    async def _execute(self, task: BackgroundTask) -> None:
        started = replace(
            task,
            status=TaskStatus.RUNNING,
            started_at=self._container.clock.now(),
        )
        await self._save(started, insert=False)
        try:
            result = await self._run_payload(started)
        except asyncio.CancelledError:
            cancelled = replace(
                started,
                status=TaskStatus.CANCELLED,
                finished_at=self._container.clock.now(),
            )
            await self._save(cancelled, insert=False)
            self._container.events.publish(
                "task.cancelled", {"task_id": task.id}, task_id=task.id
            )
        except PortError as exc:
            failed = replace(
                started,
                status=TaskStatus.FAILED,
                finished_at=self._container.clock.now(),
                error={
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                },
            )
            await self._save(failed, insert=False)
            self._container.events.publish(
                "task.failed",
                {"task_id": task.id, "code": exc.code, "message": exc.message},
                task_id=task.id,
            )
        except Exception as exc:  # noqa: BLE001 - terminal event must always fire
            failed = replace(
                started,
                status=TaskStatus.FAILED,
                finished_at=self._container.clock.now(),
                error={"code": "internal.unexpected", "message": str(exc)},
            )
            await self._save(failed, insert=False)
            self._container.events.publish(
                "task.failed",
                {
                    "task_id": task.id,
                    "code": "internal.unexpected",
                    "message": str(exc),
                },
                task_id=task.id,
            )
        else:
            completed = replace(
                started,
                status=TaskStatus.COMPLETED,
                finished_at=self._container.clock.now(),
                progress={
                    "step": result.get("steps", 0),
                    "total": result.get("steps", 0),
                },
            )
            await self._save(completed, insert=False)
            self._container.events.publish(
                "task.completed",
                {"task_id": task.id, **result},
                task_id=task.id,
            )

    async def _run_payload(self, task: BackgroundTask) -> dict[str, Any]:
        from app.application.commands.diagnostics import DiagnosticsSleepUseCase
        from app.application.dto import SleepTaskParams

        if task.type == "jobs.verify_now":
            from app.application.dto import JobIdParams
            from app.application.endpoint_verifier import EndpointVerifier

            verify_params = JobIdParams.model_validate(dict(task.params))
            return await EndpointVerifier(self._container).verify(
                job_ids=[verify_params.job_id], force=True
            )
        if task.type == "posting_intelligence.evaluate":
            from app.application.dto import PostingIntelligenceEvaluateParams
            from app.application.intelligence import RiskEvaluationService

            risk_params = PostingIntelligenceEvaluateParams.model_validate(
                dict(task.params)
            )
            return await RiskEvaluationService(self._container).evaluate(
                job_ids=risk_params.job_ids or None,
                scope=risk_params.scope,
                limit=risk_params.limit or None,
                force=risk_params.force,
                refresh_endpoints=risk_params.refresh_endpoints,
            )
        if task.type != "diagnostics.sleep":
            raise PortError(
                code="validation.invalid_params",
                message=f"unsupported task type {task.type!r}",
            )
        params = SleepTaskParams.model_validate(dict(task.params))
        return await DiagnosticsSleepUseCase(self._container).run(params, task_id=task.id)

    async def _save(self, task: BackgroundTask, *, insert: bool) -> None:
        async with self._container.uow_factory() as uow:
            if insert:
                await uow.tasks.add(task)
            else:
                await uow.tasks.save(task)
            await uow.commit()
