"""The migrator refuses newer schemas and snapshots into ``backups/`` first."""

from __future__ import annotations

import aiosqlite
import pytest
from app.infrastructure.persistence.sqlite.migrator import (
    _checksum,
    _iso_now,
    list_migration_files,
    migrate,
)

SCHEMA_MIGRATIONS = """
CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL,
  checksum TEXT NOT NULL
) STRICT
"""


@pytest.mark.asyncio
async def test_refuses_a_database_from_a_newer_build(tmp_path) -> None:
    database = tmp_path / "newer.sqlite"
    async with aiosqlite.connect(database) as conn:
        await conn.execute(SCHEMA_MIGRATIONS)
        await conn.execute(
            "INSERT INTO schema_migrations(version, name, applied_at, checksum) "
            "VALUES (9999, 'future.sql', '2026-01-01T00:00:00Z', 'x')"
        )
        await conn.commit()

    with pytest.raises(RuntimeError, match="newer version of Job Signal"):
        await migrate(database)

    async with aiosqlite.connect(database) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM schema_migrations")
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == 1, "a refused database must be left exactly as it was"


@pytest.mark.asyncio
async def test_snapshots_before_upgrading_into_the_backups_folder(tmp_path) -> None:
    database = tmp_path / "old.sqlite"
    files = list_migration_files()
    shipped = max(int(path.name.split("_", 1)[0]) for path in files)
    async with aiosqlite.connect(database) as conn:
        await conn.execute(SCHEMA_MIGRATIONS)
        for path in files:
            version = int(path.name.split("_", 1)[0])
            if version > 16:
                continue
            sql = path.read_text(encoding="utf-8")
            await conn.executescript(sql)
            await conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at, checksum) "
                "VALUES (?, ?, ?, ?)",
                (version, path.name, _iso_now(), _checksum(sql)),
            )
        await conn.commit()

    assert await migrate(database) == shipped

    snapshots = sorted((tmp_path / "backups").glob("*.bak"))
    assert len(snapshots) == 1
    assert snapshots[0].stat().st_size > 0
    assert list(tmp_path.glob("old.sqlite.*.bak")) == [], (
        "snapshots belong in backups/ so the integrity-error guidance is true"
    )
