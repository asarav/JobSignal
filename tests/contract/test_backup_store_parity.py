"""Memory/SQLite backup stores agree; the audit trail is append-only."""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite
import pytest
from app.domain.models.backup import BackupRunRecord
from app.infrastructure.persistence.memory.backups import (
    InMemoryBackupRunRepository,
    InMemoryBackupStore,
)
from app.infrastructure.persistence.sqlite.backups import (
    SqliteBackupRunRepository,
    SqliteBackupStore,
)
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory

NOW = datetime(2026, 3, 1, tzinfo=UTC)


async def _seeded_sqlite(tmp_path, name: str = "backup.sqlite"):
    db = tmp_path / name
    factory = SqliteUnitOfWorkFactory(db_path=db, clock_now=lambda: NOW)
    await factory.initialize()
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "INSERT INTO companies(id, canonical_name, normalized_name, created_at) "
            "VALUES ('co_a', 'Acme', 'acme', '2026-01-01T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO sources(id, url, kind, created_at, updated_at) "
            "VALUES ('src_1', 'https://example.com', 'ats', '2026-01-01T00:00:00Z', "
            "'2026-01-01T00:00:00Z')"
        )
        await conn.commit()
    return db


@pytest.mark.asyncio
async def test_export_then_import_round_trips_in_both_stores(tmp_path) -> None:
    source_db = await _seeded_sqlite(tmp_path, "a.sqlite")
    target_db = tmp_path / "b.sqlite"
    factory = SqliteUnitOfWorkFactory(db_path=target_db, clock_now=lambda: NOW)
    await factory.initialize()

    sqlite_source = SqliteBackupStore(source_db, data_dir=tmp_path)
    sqlite_target = SqliteBackupStore(target_db, data_dir=tmp_path)
    tables, version = await sqlite_source.export_tables()
    await sqlite_target.import_archive(tables)

    memory_source = InMemoryBackupStore(
        {
            "companies": {
                "columns": ["id", "canonical_name"],
                "rows": [["co_a", "Acme"]],
            }
        },
        data_dir=str(tmp_path),
    )
    memory_target = InMemoryBackupStore(data_dir=str(tmp_path))
    memory_tables, _ = await memory_source.export_tables()
    await memory_target.import_archive(memory_tables)

    for store, expected in (
        (sqlite_target, {"companies": 1, "sources": 1}),
        (memory_target, {"companies": 1}),
    ):
        counts = await store.table_counts()
        for name, count in expected.items():
            assert counts[name] == count
    assert version == 18
    assert (await memory_target.export_tables())[0]["companies"]["rows"] == [
        ["co_a", "Acme"]
    ]


@pytest.mark.asyncio
async def test_the_sqlite_schema_version_is_read_from_migrations(tmp_path) -> None:
    db = await _seeded_sqlite(tmp_path)
    store = SqliteBackupStore(db, data_dir=tmp_path)
    assert await store.schema_version() == 18


@pytest.mark.asyncio
async def test_backup_audit_matches_in_both_adapters(tmp_path) -> None:
    db = await _seeded_sqlite(tmp_path)
    factory = SqliteUnitOfWorkFactory(db_path=db, clock_now=lambda: NOW)
    memory = InMemoryBackupRunRepository()
    async with factory() as uow:
        sqlite = SqliteBackupRunRepository(uow._conn)  # type: ignore[attr-defined]
        for kind, at in (("export", NOW), ("import", NOW)):
            record = BackupRunRecord(
                id=f"br_{kind}",
                kind=kind,
                path="/data/backups/a.json",
                checksum="abc",
                schema_version=18,
                app_version="0.1.0",
                created_at=at,
                table_counts={"companies": 1},
            )
            await sqlite.add(record)
            await memory.add(record)
        runs_sqlite = list(await sqlite.list_runs())
        runs_memory = list(await memory.list_runs())
        with pytest.raises(aiosqlite.Error):
            await uow._conn.execute("UPDATE backup_runs SET kind = 'import'")  # type: ignore[attr-defined]
        with pytest.raises(aiosqlite.Error):
            await uow._conn.execute("DELETE FROM backup_runs")  # type: ignore[attr-defined]

    assert {row["kind"] for row in runs_sqlite} == {"export", "import"}
    assert {row["kind"] for row in runs_memory} == {"export", "import"}
    assert runs_sqlite[0]["table_counts"] == {"companies": 1}
