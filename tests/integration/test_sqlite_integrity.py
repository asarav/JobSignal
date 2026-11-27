"""A damaged database blocks startup with backup guidance (spec §17.4)."""

from __future__ import annotations

import pytest
from app.infrastructure.persistence.sqlite.migrator import migrate

MIGRATION_COUNT = 19


@pytest.mark.asyncio
async def test_a_healthy_database_passes_quick_check(tmp_path) -> None:
    db = tmp_path / "healthy.sqlite"
    version = await migrate(db)
    assert version == MIGRATION_COUNT


@pytest.mark.asyncio
async def test_a_damaged_database_is_refused(tmp_path) -> None:
    db = tmp_path / "damaged.sqlite"
    await migrate(db)
    payload = db.read_bytes()
    db.write_bytes(payload[: max(64, len(payload) // 3)])

    with pytest.raises(RuntimeError, match="integrity check failed"):
        await migrate(db)
