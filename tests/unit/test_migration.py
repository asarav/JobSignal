"""Migration checksum / 0001 unit tests."""

import asyncio

from app.infrastructure.persistence.sqlite.migrator import (
    list_migration_files,
    migrate,
    migration_checksum,
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


def test_fts5_query_escapes_special_chars():
    """User input with FTS5 special chars must not cause syntax errors."""
    # Comma stays attached to its term but is safely quoted (no syntax error)
    assert SqliteJobRepository._fts5_query("python, developer") == '"python," AND "developer"'
    assert SqliteJobRepository._fts5_query('senior "lead" engineer') == '"senior" AND """lead""" AND "engineer"'
    assert SqliteJobRepository._fts5_query("(python) developer") == '"(python)" AND "developer"'
    assert SqliteJobRepository._fts5_query("python* developer:remote") == '"python*" AND "developer:remote"'


def test_fts5_query_empty_and_whitespace():
    assert SqliteJobRepository._fts5_query("") == ""
    assert SqliteJobRepository._fts5_query("   ") == ""