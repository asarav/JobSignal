"""Migration checksum / 0001 unit tests."""

import asyncio
import sqlite3

import aiosqlite
from app.infrastructure.persistence.sqlite.migrator import (
    MIGRATION_REPAIRS,
    PRAGMAS,
    apply_pragmas,
    list_migration_files,
    migrate,
    migration_checksum,
    repair_job_titles,
)
from app.infrastructure.persistence.sqlite.repositories import SqliteJobRepository


def test_0001_has_checksum():
    files = list_migration_files()
    assert any(f.name == "0001_infrastructure.sql" for f in files)
    target = next(f for f in files if f.name == "0001_infrastructure.sql")
    assert migration_checksum(target)


def test_0003_has_checksum():
    files = list_migration_files()
    assert any(f.name == "0003_search.sql" for f in files)
    target = next(f for f in files if f.name == "0003_search.sql")
    assert migration_checksum(target)


def test_migrate_creates_db(tmp_path):
    db = tmp_path / "test.sqlite"
    version = asyncio.run(migrate(db))
    assert version >= 1
    assert db.exists()


def test_0009_backfills_workplace_from_locations():
    """Historical rows get a workplace type from their stored location text."""
    sql = (
        next(
            fixture
            for fixture in list_migration_files()
            if fixture.name == "0009_workplace_backfill.sql"
        )
    ).read_text(encoding="utf-8")
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE jobs(id TEXT, locations_json TEXT, workplace_type TEXT)")
    con.executemany(
        "INSERT INTO jobs VALUES (?, ?, 'unknown')",
        [
            ("remote", '[{"raw": "Remote India"}]'),
            ("hybrid", '[{"raw": "Budapest (Hybrid)"}]'),
            ("onsite", '[{"raw": "Berlin, on-site"}]'),
            ("plain", '[{"raw": "London"}]'),
            ("broken", "not json"),
        ],
    )
    con.executescript(sql)
    got = dict(con.execute("SELECT id, workplace_type FROM jobs"))
    assert got == {
        "remote": "remote",
        "hybrid": "hybrid",
        "onsite": "onsite",
        "plain": "unknown",
        "broken": "unknown",
    }


def test_0010_cleans_numeric_only_titles():
    sql = next(
        fixture
        for fixture in list_migration_files()
        if fixture.name == "0010_numeric_title_cleanup.sql"
    ).read_text(encoding="utf-8")
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE jobs(title TEXT, normalized_title TEXT, title_family TEXT)"
    )
    con.executemany(
        "INSERT INTO jobs VALUES (?, ?, ?)",
        [
            ("4083293003", "4083293003", "4083293003"),
            ("Backend Engineer 2", "backend engineer 2", "backend engineer"),
            ("", "", ""),
        ],
    )
    con.executescript(sql)
    got = con.execute("SELECT title, normalized_title, title_family FROM jobs").fetchall()
    assert got == [
        ("Untitled", "untitled", "untitled"),
        ("Backend Engineer 2", "backend engineer 2", "backend engineer"),
        ("", "", ""),
    ]


def test_0011_repairs_unicode_normalized_titles(tmp_path):
    """Accent-stripped derived titles are recomputed by the Python repair."""
    db = tmp_path / "jobs.sqlite"

    async def run():
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "CREATE TABLE jobs(id TEXT PRIMARY KEY, title TEXT NOT NULL, "
                "normalized_title TEXT NOT NULL, title_family TEXT)"
            )
            await conn.executemany(
                "INSERT INTO jobs VALUES (?, ?, ?, ?)",
                [
                    (
                        "unicode",
                        "Analyste de systèmes cliniques",
                        "analyste de syst mes cliniques",
                        "analyste de syst mes",
                    ),
                    ("plain", "Backend Engineer", "backend engineer", "backend engineer"),
                ],
            )
            await repair_job_titles(conn)
            await conn.commit()
            cur = await conn.execute(
                "SELECT id, normalized_title, title_family FROM jobs ORDER BY id"
            )
            return await cur.fetchall()

    assert asyncio.run(run()) == [
        ("plain", "backend engineer", "backend engineer"),
        ("unicode", "analyste de systemes cliniques", "analyste de systemes"),
    ]


def test_0011_repair_is_wired_to_the_migration():
    assert MIGRATION_REPAIRS[11] is repair_job_titles


def test_fts5_query_escapes_special_chars():
    """User input with FTS5 special chars must not cause syntax errors."""
    # Comma stays attached to its term but is safely quoted (no syntax error)
    assert SqliteJobRepository._fts5_query("python, developer") == '"python," AND "developer"'
    assert SqliteJobRepository._fts5_query('senior "lead" engineer') == (
        '"senior" AND """lead""" AND "engineer"'
    )


def test_fts5_query_empty_and_whitespace():
    assert SqliteJobRepository._fts5_query("") == ""
    assert SqliteJobRepository._fts5_query("   ") == ""


def test_pragmas_do_not_switch_the_journal_mode():
    """The switch needs a database-wide lock, so it cannot run per connection."""
    assert not any("journal_mode" in pragma for pragma in PRAGMAS)


def test_migrate_leaves_the_database_in_wal(tmp_path):
    """WAL is switched once, while migration is the only connection open."""
    db = tmp_path / "wal.sqlite"
    asyncio.run(migrate(db))
    conn = sqlite3.connect(db)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert str(mode).lower() == "wal"


def test_opening_a_connection_does_not_need_a_journal_mode_switch(tmp_path):
    """A connection opens cleanly while another holds the write lock.

    This is the "database is locked" a crawl reported on first start.  A
    database that is not in WAL -- a data directory that cannot host SQLite's
    shared-memory WAL keeps its own journal mode -- plus a per-connection
    ``PRAGMA journal_mode = WAL`` makes SQLite answer with a busy error
    immediately, without waiting out ``busy_timeout``.
    """
    db = tmp_path / "delete_mode.sqlite"
    holder = sqlite3.connect(db, isolation_level=None)
    try:
        assert holder.execute("PRAGMA journal_mode = DELETE").fetchone()[0] == "delete"
        holder.execute("CREATE TABLE t(x)")
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO t VALUES (1)")

        async def open_a_connection() -> None:
            async with aiosqlite.connect(db) as conn:
                await apply_pragmas(conn)

        asyncio.run(open_a_connection())
    finally:
        holder.close()