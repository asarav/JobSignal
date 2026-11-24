"""SQLite append-only fit-weight history."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import aiosqlite

from app.domain.models.fit_weight import FitWeightVersionRecord


def _row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["weights"] = json.loads(data.pop("weights_json") or "{}")
    previous = data.pop("previous_weights_json", None)
    data["previous_weights"] = json.loads(previous) if previous else None
    data["evidence"] = json.loads(data.pop("evidence_json") or "{}")
    return data


class SqliteFitWeightRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(self, record: FitWeightVersionRecord) -> None:
        await self._conn.execute(
            """
            INSERT INTO fit_weight_versions(
              id, profile_id, weights_json, previous_weights_json, source,
              evidence_json, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.profile_id,
                json.dumps(record.weights, sort_keys=True),
                json.dumps(record.previous_weights, sort_keys=True)
                if record.previous_weights is not None
                else None,
                record.source,
                json.dumps(record.evidence, sort_keys=True),
                record.created_by,
                record.created_at.isoformat(),
            ),
        )
        await self._conn.commit()

    async def list_for_profile(
        self, profile_id: str, *, limit: int = 50
    ) -> Sequence[Mapping[str, object]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            """
            SELECT * FROM fit_weight_versions
            WHERE profile_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (profile_id, limit),
        )
        return [_row(row) for row in await cur.fetchall()]

    async def latest_for_profile(
        self, profile_id: str
    ) -> Mapping[str, object] | None:
        rows = await self.list_for_profile(profile_id, limit=1)
        return rows[0] if rows else None
