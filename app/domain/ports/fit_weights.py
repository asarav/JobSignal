"""Port for append-only fit-weight history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from app.domain.models.fit_weight import FitWeightVersionRecord


class FitWeightRepository(Protocol):
    async def add(self, record: FitWeightVersionRecord) -> None: ...

    async def list_for_profile(
        self, profile_id: str, *, limit: int = 50
    ) -> Sequence[Mapping[str, object]]: ...

    async def latest_for_profile(
        self, profile_id: str
    ) -> Mapping[str, object] | None: ...
