"""sources.* handlers."""

from __future__ import annotations

from app.application.commands.sources import SourcesUseCase
from app.application.dto import (
    EmptyParams,
    SourceAddParams,
    SourceIdParams,
    SourceSetEnabledParams,
    SourceSetScheduleParams,
)


class SourcesHandlers:
    def __init__(self, sources: SourcesUseCase) -> None:
        self._sources = sources

    async def list(self, params: EmptyParams) -> dict:
        return (await self._sources.list()).model_dump()

    async def add(self, params: SourceAddParams) -> dict:
        return (await self._sources.add(params)).model_dump()

    async def set_enabled(self, params: SourceSetEnabledParams) -> dict:
        await self._sources.set_enabled(params)
        return {"ok": True}

    async def set_schedule(self, params: SourceSetScheduleParams) -> dict:
        await self._sources.set_schedule(params)
        return {"ok": True}

    async def delete(self, params: SourceIdParams) -> dict:
        await self._sources.delete(params)
        return {"ok": True}
