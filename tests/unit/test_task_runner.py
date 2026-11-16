"""Task runner lifecycle: persistence plus progress/completed events."""

from __future__ import annotations

import asyncio

import pytest
from tests.support.containers import build_test_container
from tests.support.fakes import FakeEventPublisher


async def _wait_status(container, task_id: str, wanted: str, attempts: int = 100):
    stored = None
    for _ in range(attempts):
        async with container.uow_factory() as uow:
            stored = await uow.tasks.get(task_id)
        if stored is not None and stored.status.value == wanted:
            return stored
        await asyncio.sleep(0.01)
    return stored


@pytest.mark.asyncio
async def test_sleep_task_completes_and_emits_events():
    events = FakeEventPublisher()
    container = build_test_container(events=events)
    runner = container.task_runner
    assert runner is not None
    await runner.start()
    try:
        task = await runner.submit("diagnostics.sleep", {"seconds": 0, "steps": 3})
        stored = await _wait_status(container, task.id, "completed")

        assert stored is not None
        assert stored.status.value == "completed"
        assert stored.progress["step"] == 3
        topics = [event["topic"] for event in events.events]
        assert topics[0] == "task.queued"
        assert topics.count("task.progress") == 3
        assert topics[-1] == "task.completed"
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_unknown_task_type_fails_cleanly():
    events = FakeEventPublisher()
    container = build_test_container(events=events)
    runner = container.task_runner
    assert runner is not None
    await runner.start()
    try:
        task = await runner.submit("not.a.task", {})
        stored = await _wait_status(container, task.id, "failed")
        assert stored is not None
        assert stored.status.value == "failed"
        assert stored.error is not None
        assert stored.error["code"] == "validation.invalid_params"
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_interrupted_tasks_are_marked_on_startup():
    container = build_test_container()
    async with container.uow_factory() as uow:
        from datetime import UTC, datetime

        from app.domain.models.enums import TaskStatus
        from app.domain.models.tasks import BackgroundTask

        await uow.tasks.add(
            BackgroundTask(
                id="old",
                type="diagnostics.sleep",
                status=TaskStatus.RUNNING,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        changed = await uow.tasks.mark_interrupted()
        await uow.commit()
        stored = await uow.tasks.get("old")

    assert changed == 1
    assert stored is not None
    assert stored.status.value == "interrupted"
