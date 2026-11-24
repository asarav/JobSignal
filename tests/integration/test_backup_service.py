"""Backup export/import: confirmation, no overwrite, verified round trip."""

from __future__ import annotations

import shutil

import aiosqlite
import pytest
from app.application.backup import BackupService
from app.domain.models.common import PortError
from tests.support.containers import build_test_container


def _container(tmp_path, name: str):
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    container = build_test_container(
        data_dir=str(root), sqlite_db=root / "jobs.sqlite"
    )
    return container


async def _seed(container) -> None:
    await container.uow_factory.initialize()
    async with aiosqlite.connect(container.uow_factory.db_path) as conn:
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


@pytest.mark.asyncio
async def test_export_then_import_verifies_and_never_overwrites(tmp_path) -> None:
    source = _container(tmp_path, "source")
    await _seed(source)
    exported = await BackupService(source).export()
    assert exported["ok"] is True
    assert exported["counts"]["companies"] == 1
    assert (tmp_path / "source" / "backups").exists()

    history = await BackupService(source).history()
    assert [run["kind"] for run in history["runs"]] == ["export"]

    target = _container(tmp_path, "target")
    await target.uow_factory.initialize()
    (tmp_path / "target" / "backups").mkdir(parents=True, exist_ok=True)
    shutil.copy(
        exported["path"],
        tmp_path / "target" / "backups" / "import.json",
    )

    with pytest.raises(PortError) as unconfirmed:
        await BackupService(target).import_archive(path="backups/import.json")
    assert unconfirmed.value.code == "validation.confirmation_required"

    imported = await BackupService(target).import_archive(
        path="backups/import.json", confirm=True
    )
    assert imported["verified"] is True
    assert imported["counts"]["companies"] == 1
    assert imported["counts"]["sources"] == 1

    with pytest.raises(PortError) as occupied:
        await BackupService(target).import_archive(
            path="backups/import.json", confirm=True
        )
    assert occupied.value.code == "conflict.backup_not_empty"
    assert occupied.value.details["table_counts"]["companies"] == 1


@pytest.mark.asyncio
async def test_a_tampered_backup_is_rejected(tmp_path) -> None:
    container = _container(tmp_path, "tamper")
    await _seed(container)
    exported = await BackupService(container).export(path="backups/a.json")
    path = tmp_path / "tamper" / "backups" / "a.json"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("Acme", "Acmf"), encoding="utf-8")

    fresh = _container(tmp_path, "tamper-target")
    await fresh.uow_factory.initialize()
    (tmp_path / "tamper-target" / "backups").mkdir(parents=True, exist_ok=True)
    shutil.copy(path, tmp_path / "tamper-target" / "backups" / "a.json")
    with pytest.raises(PortError) as bad:
        await BackupService(fresh).import_archive(path="backups/a.json", confirm=True)
    assert bad.value.code == "validation.invalid_backup"
    assert exported["checksum"]


@pytest.mark.asyncio
async def test_backup_paths_stay_inside_the_data_directory(tmp_path) -> None:
    container = _container(tmp_path, "escape")
    await container.uow_factory.initialize()
    with pytest.raises(PortError) as outside:
        await BackupService(container).export(path=str(tmp_path / "elsewhere.json"))
    assert outside.value.code == "validation.invalid_params"
