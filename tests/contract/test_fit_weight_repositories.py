"""Append-only fit-weight history behaves the same in memory and SQLite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest
from app.domain.models.fit_weight import FitWeightVersionRecord
from app.infrastructure.persistence.memory.weights import InMemoryFitWeightRepository
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory
from app.infrastructure.persistence.sqlite.weights import SqliteFitWeightRepository

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _version(
    id_: str, weights: dict[str, float], *, previous: dict[str, float] | None, at: datetime
) -> FitWeightVersionRecord:
    return FitWeightVersionRecord(
        id=id_,
        profile_id="default",
        weights=weights,
        previous_weights=previous,
        source="proposal" if previous else "manual",
        created_by="user",
        created_at=at,
        evidence={"sample_size": 24},
    )


@pytest.mark.asyncio
async def test_history_keeps_every_version_newest_first(tmp_path) -> None:
    db = tmp_path / "weights.sqlite"
    factory = SqliteUnitOfWorkFactory(db_path=db, clock_now=lambda: NOW)
    await factory.initialize()

    memory = InMemoryFitWeightRepository()
    async with factory() as uow:
        sqlite = SqliteFitWeightRepository(uow._conn)  # type: ignore[attr-defined]
        first = _version("fwt_1", {"skills_resume": 0.4}, previous=None, at=NOW)
        second = _version(
            "fwt_2",
            {"skills_resume": 0.45},
            previous={"skills_resume": 0.4},
            at=NOW + timedelta(minutes=1),
        )
        for repo in (memory, sqlite):
            await repo.add(first)
            await repo.add(second)

        for repo in (memory, sqlite):
            versions = list(await repo.list_for_profile("default"))
            assert [row["id"] for row in versions] == ["fwt_2", "fwt_1"]
            assert versions[0]["previous_weights"] == {"skills_resume": 0.4}
            assert versions[1]["previous_weights"] is None
            latest = await repo.latest_for_profile("default")
            assert latest is not None and latest["id"] == "fwt_2"
            assert await repo.list_for_profile("other") == []


@pytest.mark.asyncio
async def test_sqlite_weight_history_is_immutable(tmp_path) -> None:
    db = tmp_path / "weights.sqlite"
    factory = SqliteUnitOfWorkFactory(db_path=db, clock_now=lambda: NOW)
    await factory.initialize()
    async with factory() as uow:
        await SqliteFitWeightRepository(uow._conn).add(  # type: ignore[attr-defined]
            _version("fwt_1", {"skills_resume": 0.4}, previous=None, at=NOW)
        )
        await uow.commit()

    async with aiosqlite.connect(db) as conn:
        with pytest.raises(aiosqlite.Error):
            await conn.execute("UPDATE fit_weight_versions SET source = 'manual'")
        with pytest.raises(aiosqlite.Error):
            await conn.execute("DELETE FROM fit_weight_versions")
