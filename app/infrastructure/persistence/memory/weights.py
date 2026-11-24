"""In-memory twin of the append-only fit-weight history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.domain.models.fit_weight import FitWeightVersionRecord


class InMemoryFitWeightRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []
        self._sequence = 0

    async def add(self, record: FitWeightVersionRecord) -> None:
        self._sequence += 1
        self.records.append(
            {
                "_seq": self._sequence,
                "id": record.id,
                "profile_id": record.profile_id,
                "weights": dict(record.weights),
                "previous_weights": (
                    dict(record.previous_weights)
                    if record.previous_weights is not None
                    else None
                ),
                "source": record.source,
                "evidence": dict(record.evidence),
                "created_by": record.created_by,
                "created_at": record.created_at.isoformat(),
            }
        )

    async def list_for_profile(
        self, profile_id: str, *, limit: int = 50
    ) -> Sequence[Mapping[str, object]]:
        rows = [
            dict(row) for row in self.records if row["profile_id"] == profile_id
        ]
        rows.sort(
            key=lambda row: (str(row["created_at"]), int(row.get("_seq") or 0)),
            reverse=True,
        )
        for row in rows:
            row.pop("_seq", None)
        return rows[:limit]

    async def latest_for_profile(
        self, profile_id: str
    ) -> Mapping[str, object] | None:
        rows = await self.list_for_profile(profile_id, limit=1)
        return rows[0] if rows else None
