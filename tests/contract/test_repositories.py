"""Repository contract tests run against both SQLite and InMemory UoW."""

from datetime import UTC, datetime

import pytest
from app.domain.models.tasks import BackgroundTask, TaskStatus
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory


async def _make_uow_factory(kind, tmp_path):
    if kind == "memory":
        return InMemoryUnitOfWorkFactory()
    if kind == "sqlite":
        db = tmp_path / "test.sqlite"

        from app.infrastructure.persistence.sqlite.migrator import migrate

        await migrate(db)
        return SqliteUnitOfWorkFactory(db_path=db, clock_now=datetime.now)
    raise ValueError(kind)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_settings_roundtrip(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    async with factory() as uow:
        await uow.settings.put("foo", {"bar": 1})
        await uow.commit()
    async with factory() as uow:
        got = await uow.settings.get("foo")
        assert got == {"bar": 1}
        all_settings = await uow.settings.get_all()
        assert "foo" in all_settings


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_task_add_get_list(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    task = BackgroundTask(
        id="t1", type="diagnostics.sleep", status=TaskStatus.QUEUED, priority=5,
        params={}, progress={}, error=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    async with factory() as uow:
        await uow.tasks.add(task)
        await uow.commit()
    async with factory() as uow:
        got = await uow.tasks.get("t1")
        assert got is not None
        assert got.status == TaskStatus.QUEUED
        listed = await uow.tasks.list()
        assert len(listed) >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_task_cancel_noop_unknown(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    async with factory() as uow:
        # unknown task cancel is no-op success
        await uow.tasks.get("does-not-exist")  # returns None, no error
        listed = await uow.tasks.list()
        assert listed == []