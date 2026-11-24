"""Upgrade a 0017 database to 0018: backup audit is append-only."""

from __future__ import annotations

import aiosqlite
import pytest
from app.infrastructure.persistence.sqlite.migrator import (
    _checksum,
    _iso_now,
    list_migration_files,
    migrate,
)


@pytest.mark.asyncio
async def test_upgrade_adds_append_only_backup_audit(tmp_path) -> None:
    db = tmp_path / "upgrade.sqlite"
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            """
            CREATE TABLE schema_migrations (
              version INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              applied_at TEXT NOT NULL,
              checksum TEXT NOT NULL
            ) STRICT
            """
        )
        for path in list_migration_files():
            version = int(path.name.split("_", 1)[0])
            if version > 17:
                continue
            sql = path.read_text(encoding="utf-8")
            await conn.executescript(sql)
            await conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at, checksum) "
                "VALUES (?, ?, ?, ?)",
                (version, path.name, _iso_now(), _checksum(sql)),
            )
        await conn.execute(
            "INSERT INTO companies(id, canonical_name, normalized_name, created_at) "
            "VALUES ('co_a', 'Acme', 'acme', '2026-01-01T00:00:00Z')"
        )
        await conn.commit()

    assert await migrate(db) == 18
    assert await migrate(db) == 18

    async with aiosqlite.connect(db) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        assert "backup_runs" in {row[0] for row in await cur.fetchall()}
        cur = await conn.execute("SELECT canonical_name FROM companies")
        assert (await cur.fetchone())[0] == "Acme"
        await conn.execute(
            "INSERT INTO backup_runs(id, kind, path, checksum, schema_version, "
            "app_version, created_at) VALUES ('br_1', 'export', '/data/a.json', "
            "'abc', 18, '0.1.0', '2026-03-01T00:00:00Z')"
        )
        await conn.commit()
        with pytest.raises(aiosqlite.Error):
            await conn.execute("DELETE FROM backup_runs")
