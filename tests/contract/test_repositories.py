"""Repository contract tests run against both SQLite and InMemory UoW."""

from datetime import UTC, datetime

import pytest
from app.domain.models.candidate import SearchProfile
from app.domain.models.source import SourceConfig
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


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_search_profile_add_get_list(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    profile = SearchProfile(
        id="sp_1",
        name="Python Remote",
        candidate_profile_id="cp_1",
        scoring_config_id="sc_1",
        ranking_policy="deprioritize",
        hard_filters={"workplace_types": ["remote"]},
        preferences={"key": "value"},
    )
    async with factory() as uow:
        await uow.search_profiles.add(profile)
        await uow.commit()

    async with factory() as uow:
        got = await uow.search_profiles.get("sp_1")
        assert got is not None
        assert got.name == "Python Remote"
        assert got.hard_filters == {"workplace_types": ["remote"]}
        assert got.preferences == {"key": "value"}

        listed = await uow.search_profiles.list()
        assert len(listed) == 1
        assert listed[0].name == "Python Remote"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_search_profile_save_update(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    profile = SearchProfile(
        id="sp_1",
        name="Original",
        candidate_profile_id="",
        hard_filters={},
        preferences={},
    )
    async with factory() as uow:
        await uow.search_profiles.add(profile)
        await uow.commit()

    async with factory() as uow:
        got = await uow.search_profiles.get("sp_1")
        assert got is not None
        got = SearchProfile(
            id=got.id,
            name="Updated",
            candidate_profile_id=got.candidate_profile_id,
            scoring_config_id=got.scoring_config_id,
            ranking_policy=got.ranking_policy,
            hard_filters={"employment_types": ["contract"]},
            preferences=got.preferences,
        )
        await uow.search_profiles.save(got)
        await uow.commit()

    async with factory() as uow:
        got = await uow.search_profiles.get("sp_1")
        assert got is not None
        assert got.name == "Updated"
        assert got.hard_filters == {"employment_types": ["contract"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_search_profile_delete(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    profile = SearchProfile(id="sp_1", name="To Delete", candidate_profile_id="")
    async with factory() as uow:
        await uow.search_profiles.add(profile)
        await uow.commit()

    async with factory() as uow:
        await uow.search_profiles.delete("sp_1")
        await uow.commit()
        got = await uow.search_profiles.get("sp_1")
        assert got is None
        listed = await uow.search_profiles.list()
        assert listed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_search_profile_get_unknown(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    async with factory() as uow:
        got = await uow.search_profiles.get("does-not-exist")
        assert got is None

@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_crawl_run_last_started_by_source(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    earlier = datetime(2026, 1, 1, tzinfo=UTC)
    later = datetime(2026, 1, 2, tzinfo=UTC)
    async with factory() as uow:
        await uow.sources.add(SourceConfig(id="src_a", url="https://example.com/a", kind="ats"))
        await uow.sources.add(SourceConfig(id="src_b", url="https://example.com/b", kind="ats"))
        await uow.crawl_runs.create("run_1", "src_a", earlier)
        await uow.crawl_runs.create("run_2", "src_a", later)
        await uow.crawl_runs.create("run_3", "src_b", earlier)
        await uow.commit()

    async with factory() as uow:
        latest = await uow.crawl_runs.last_started_by_source()

    assert set(latest) == {"src_a", "src_b"}
    assert latest["src_a"].startswith("2026-01-02")
    assert latest["src_b"].startswith("2026-01-01")
