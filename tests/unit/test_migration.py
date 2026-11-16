"""Migration checksum / 0001 unit tests."""

import asyncio

from app.infrastructure.persistence.sqlite.migrator import (
    list_migration_files,
    migrate,
    migration_checksum,
)


def test_0001_has_checksum():
    files = list_migration_files()
    assert any(f.name == "0001_infrastructure.sql" for f in files)
    target = next(f for f in files if f.name == "0001_infrastructure.sql")
    assert migration_checksum(target)


def test_migrate_creates_db(tmp_path):
    db = tmp_path / "test.sqlite"
    version = asyncio.run(migrate(db))
    assert version >= 1
    assert db.exists()