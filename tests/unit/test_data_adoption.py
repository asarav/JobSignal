"""Adopt a previous installation's database, then remove the old folder."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from app.infrastructure.persistence.sqlite import adoption
from app.infrastructure.persistence.sqlite.adoption import (
    DB_NAME,
    RECORD_NAME,
    adopt_previous_install,
)
from app.infrastructure.persistence.sqlite.migrator import migrate


def _install_dir(roots: list[Path], identifier: str) -> Path:
    return roots[0] / identifier / "worker"


async def _seed(roots: list[Path], identifier: str, marker: str) -> tuple[Path, int]:
    """Write a migrated database carrying one row of recognisable data."""
    folder = _install_dir(roots, identifier)
    folder.mkdir(parents=True, exist_ok=True)
    database = folder / DB_NAME
    version = await migrate(database)
    conn = sqlite3.connect(database)
    try:
        conn.execute("CREATE TABLE adoption_probe(value TEXT NOT NULL)")
        conn.execute("INSERT INTO adoption_probe(value) VALUES (?)", (marker,))
        conn.commit()
    finally:
        conn.close()
    return database, version


def _probe(database: Path) -> str:
    conn = sqlite3.connect(database)
    try:
        row = conn.execute("SELECT value FROM adoption_probe").fetchone()
    finally:
        conn.close()
    assert row is not None
    return str(row[0])


@pytest.mark.asyncio
async def test_adopts_the_previous_install_and_removes_it(tmp_path) -> None:
    roots = [tmp_path]
    legacy, version = await _seed(roots, "com.jobsignal.app", "kept")
    legacy_folder = legacy.parent
    target = tmp_path / "com.jobsignal.desktop" / "worker"

    result = adopt_previous_install(target, roots=roots, environ={})

    assert result.adopted is True
    assert result.removed is True
    assert result.source == legacy
    assert result.schema_version == version
    assert _probe(target / DB_NAME) == "kept"
    assert not legacy_folder.exists(), "the previous data folder is retired"
    assert not legacy_folder.parent.exists(), "the empty app folder goes with it"
    record = json.loads((target / RECORD_NAME).read_text(encoding="utf-8"))
    assert record["source"] == str(legacy)
    assert record["schema_version"] == version
    assert record["previous_folder_removed"] is True


@pytest.mark.asyncio
async def test_keeps_the_previous_folder_when_removal_fails(
    tmp_path, monkeypatch
) -> None:
    roots = [tmp_path]
    legacy, _ = await _seed(roots, "com.jobsignal.app", "kept")
    target = tmp_path / "com.jobsignal.desktop" / "worker"

    real_rmtree = shutil.rmtree

    def guarded(path, *args, **kwargs):
        if Path(path) == legacy.parent.resolve():
            raise OSError("file is in use")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(adoption.shutil, "rmtree", guarded)

    result = adopt_previous_install(target, roots=roots, environ={})

    assert result.adopted is True
    assert result.removed is False
    assert _probe(target / DB_NAME) == "kept"
    assert legacy.is_file(), "a failed removal must not cost the data"
    record = json.loads((target / RECORD_NAME).read_text(encoding="utf-8"))
    assert record["previous_folder_removed"] is False


@pytest.mark.asyncio
async def test_keeps_the_database_the_new_install_already_wrote(tmp_path) -> None:
    roots = [tmp_path]
    legacy, _ = await _seed(roots, "com.jobsignal.app", "old")
    target = tmp_path / "com.jobsignal.desktop" / "worker"
    await _seed(roots, "com.jobsignal.desktop", "current")

    result = adopt_previous_install(target, roots=roots, environ={})

    assert result.adopted is False
    assert "already has a database" in result.reason
    assert _probe(target / DB_NAME) == "current"
    assert legacy.is_file(), "an untouched install keeps its own data"


def test_ignores_a_damaged_previous_database(tmp_path) -> None:
    roots = [tmp_path]
    folder = _install_dir(roots, "com.jobsignal.app")
    folder.mkdir(parents=True)
    damaged = folder / DB_NAME
    damaged.write_bytes(b"not a database" * 64)
    target = tmp_path / "com.jobsignal.desktop" / "worker"

    result = adopt_previous_install(target, roots=roots, environ={})

    assert result.adopted is False
    assert "integrity" in result.reason
    assert not (target / DB_NAME).exists()
    assert damaged.is_file(), "a refused database is never deleted"


def test_refuses_a_database_from_a_newer_build(tmp_path) -> None:
    roots = [tmp_path]
    folder = _install_dir(roots, "com.jobsignal.app")
    folder.mkdir(parents=True)
    database = folder / DB_NAME
    conn = sqlite3.connect(database)
    try:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO schema_migrations(version) VALUES (9999)")
        conn.commit()
    finally:
        conn.close()
    target = tmp_path / "com.jobsignal.desktop" / "worker"

    result = adopt_previous_install(target, roots=roots, environ={})

    assert result.adopted is False
    assert "newer build" in result.reason
    assert not (target / DB_NAME).exists()
    assert database.is_file(), "a refused database is never deleted"


@pytest.mark.asyncio
async def test_ignores_directories_the_installer_does_not_own(tmp_path) -> None:
    roots = [tmp_path]
    legacy, _ = await _seed(roots, "com.jobsignal.app", "kept")

    result = adopt_previous_install(tmp_path / "scratch", roots=roots, environ={})

    assert result.adopted is False
    assert "not the installed app's data directory" in result.reason
    assert legacy.is_file()


@pytest.mark.asyncio
async def test_an_explicit_data_directory_disables_adoption(tmp_path) -> None:
    roots = [tmp_path]
    legacy, _ = await _seed(roots, "com.jobsignal.app", "kept")
    target = tmp_path / "com.jobsignal.desktop" / "worker"

    result = adopt_previous_install(
        target, roots=roots, environ={"LODESTAR_DATA_DIR": str(target)}
    )

    assert result.adopted is False
    assert "authoritative" in result.reason
    assert not (target / DB_NAME).exists()
    assert legacy.is_file()


@pytest.mark.asyncio
async def test_prefers_the_most_recently_written_previous_install(tmp_path) -> None:
    roots = [tmp_path]
    older, _ = await _seed(roots, "com.jobsignal.app", "old")
    newer, _ = await _seed(roots, "com.jobsignal.legacy", "new")
    os.utime(older, (1_000_000_000, 1_000_000_000))
    os.utime(newer, (1_000_000_100, 1_000_000_100))
    target = tmp_path / "com.jobsignal.desktop" / "worker"

    result = adopt_previous_install(
        target,
        roots=roots,
        identifiers=("com.jobsignal.app", "com.jobsignal.legacy"),
        environ={},
    )

    assert result.adopted is True
    assert result.source == newer
    assert _probe(target / DB_NAME) == "new"
    assert not newer.parent.exists(), "the adopted folder is removed"
    assert older.is_file(), "installations that were not adopted are untouched"
