"""Upgrade an existing 0014 database to 0015/0016 without losing anything."""

from __future__ import annotations

import aiosqlite
import pytest
from app.infrastructure.persistence.sqlite.migrator import (
    _checksum,
    _iso_now,
    list_migration_files,
    migrate,
)


async def _database_at_0014(tmp_path) -> object:
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
            if version > 14:
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
        await conn.execute(
            "INSERT INTO companies(id, canonical_name, normalized_name, created_at) "
            "VALUES ('co_b', 'Acme Inc', 'acme inc', '2026-01-01T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO sources(id, url, kind, created_at, updated_at) "
            "VALUES ('src_1', 'https://example.com', 'ats', '2026-01-01T00:00:00Z', "
            "'2026-01-01T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO jobs(id, company_id, source_id, title, normalized_title, "
            "canonical_url, first_seen_at, last_seen_at, fetched_at, created_at, "
            "updated_at) VALUES ('job_1', 'co_b', 'src_1', 'Data Engineer', "
            "'data engineer', 'https://example.com/job_1', '2026-01-01T00:00:00Z', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        await conn.commit()
    return db


@pytest.mark.asyncio
async def test_upgrade_applies_only_the_new_migrations(tmp_path) -> None:
    db = await _database_at_0014(tmp_path)

    version = await migrate(db)
    again = await migrate(db)

    assert version == 19
    assert again == 19

    async with aiosqlite.connect(db) as conn:
        cur = await conn.execute("SELECT MAX(version) FROM schema_migrations")
        assert (await cur.fetchone())[0] == 19
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        tables = {row[0] for row in await cur.fetchall()}
        assert {"company_merges", "job_keywords", "job_keyword_revisions"} <= tables
        cur = await conn.execute("SELECT canonical_name FROM companies WHERE id='co_a'")
        assert (await cur.fetchone())[0] == "Acme"
        cur = await conn.execute("SELECT company_id FROM jobs WHERE id='job_1'")
        assert (await cur.fetchone())[0] == "co_b"

    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "INSERT INTO company_merges(id, survivor_id, merged_id, action, "
            "created_at) VALUES ('cm_1', 'co_a', 'co_b', 'merge', "
            "'2026-03-01T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO job_keyword_revisions(id, job_id, revision_hash, "
            "taxonomy_version, keyword_count, extracted_at) VALUES ('rev_1', "
            "'job_1', 'h1', 'taxonomy.v1', 1, '2026-03-01T00:00:00Z')"
        )
        await conn.commit()
