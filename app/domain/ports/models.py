from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.domain.models.common import PortError, Result


class ModelProvider(Protocol):
    id: str
    capabilities: frozenset[str]

    async def embed(
        self, texts: Sequence[str]
    ) -> Result[Sequence[Sequence[float]], PortError]: ...
