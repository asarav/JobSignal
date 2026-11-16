"""S2: settings/task suite is equal on InMemoryUnitOfWork vs SQLite."""

from datetime import UTC, datetime

import pytest
from app.application.commands.settings import SettingsUseCase
from app.application.commands.tasks import TasksUseCase
from app.application.dto import CancelTaskParams, SettingsPatch, TaskFilter
from app.domain.models.enums import TaskStatus
from app.domain.models.tasks import BackgroundTask
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from app.infrastructure.persistence.sqlite.migrator import migrate
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory
from tests.support.containers import build_test_container
from tests.support.fakes import FakeClock


async def _run_suite(factory) -> dict:
    container = build_test_container(uow_factory=factory)
    settings = SettingsUseCase(container)
    tasks = TasksUseCase(container)

    await settings.update(SettingsPatch(patch={"http": {"timeout_s": 12.5}}))
    stored = await settings.get()

    task = BackgroundTask(
        id="t1",
        type="diagnostics.sleep",
        status=TaskStatus.QUEUED,
        priority=5,
        params={},
        progress={},
        error=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    async with factory() as uow:
        await uow.tasks.add(task)
        await uow.commit()

    listed = await tasks.list_tasks(TaskFilter())
    await tasks.cancel_task(CancelTaskParams(task_id="t1"))
    async with factory() as uow:
        cancelled = await uow.tasks.get("t1")

    return {
        "timeout_s": stored.config["http"]["timeout_s"],
        "overrides": stored.overrides,
        "tasks": [(t.id, t.type, t.status.value) for t in listed],
        "cancelled_status": cancelled.status.value if cancelled else None,
    }


@pytest.mark.asyncio
async def test_settings_tasks_equal_memory_vs_sqlite(tmp_path):
    memory = await _run_suite(InMemoryUnitOfWorkFactory())

    db = tmp_path / "s2.sqlite"
    await migrate(db)
    sqlite_factory = SqliteUnitOfWorkFactory(db_path=db, clock_now=FakeClock().now)
    sqlite = await _run_suite(sqlite_factory)

    assert memory == sqlite
    assert memory["timeout_s"] == 12.5
    assert memory["cancelled_status"] == "cancelled"
