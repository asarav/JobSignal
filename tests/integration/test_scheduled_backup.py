"""Scheduled local backups: interval, retention, and manual exports untouched."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.application.backup import BackupService
from app.application.config import AppConfig
from tests.support.containers import build_test_container
from tests.support.fakes import FakeClock

START = datetime(2026, 3, 1, tzinfo=UTC)


def _container(tmp_path, clock, *, keep: int = 1, enabled: bool = True):
    config = AppConfig(backup={"enabled": enabled, "interval_days": 7, "keep": keep})
    return build_test_container(
        data_dir=str(tmp_path), clock=clock, config=config.with_data_dir(str(tmp_path))
    )


@pytest.mark.asyncio
async def test_the_first_check_only_records_a_baseline(tmp_path) -> None:
    clock = FakeClock(START)
    service = BackupService(_container(tmp_path, clock))
    assert await service.run_if_due() is None
    assert not (tmp_path / "backups").exists()
    assert await service.run_if_due() is None


@pytest.mark.asyncio
async def test_a_due_backup_is_written_and_pruned(tmp_path) -> None:
    clock = FakeClock(START)
    service = BackupService(_container(tmp_path, clock, keep=1))
    await service.run_if_due()
    await service.export(path="backups/manual-before-upgrade.json")

    clock.advance(8 * 86_400)
    first = await service.run_if_due()
    assert first is not None
    first_path = first["path"]

    clock.advance(8 * 86_400)
    second = await service.run_if_due()
    assert second is not None

    folder = tmp_path / "backups"
    auto = sorted(path.name for path in folder.glob("jobsignal-auto-*.json"))
    assert len(auto) == 1
    assert (folder / "manual-before-upgrade.json").exists()
    assert not Path(first_path).exists(), "the oldest automatic archive is pruned"


@pytest.mark.asyncio
async def test_disabled_backups_write_nothing(tmp_path) -> None:
    clock = FakeClock(START)
    service = BackupService(_container(tmp_path, clock, enabled=False))
    assert await service.run_if_due() is None
    clock.advance(30 * 86_400)
    assert await service.run_if_due() is None
    assert not (tmp_path / "backups").exists()
