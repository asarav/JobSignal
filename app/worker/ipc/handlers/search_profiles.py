"""search_profiles.* handlers."""

from __future__ import annotations

from app.application.commands.search_profiles import SearchProfilesUseCase
from app.application.dto import (
    EmptyParams,
    SearchProfileParams,
    SearchProfileSaveParams,
)


class SearchProfilesHandlers:
    def __init__(self, profiles: SearchProfilesUseCase) -> None:
        self._profiles = profiles

    async def list(self, params: EmptyParams) -> dict:
        return (await self._profiles.list()).model_dump()

    async def save(self, params: SearchProfileSaveParams) -> dict:
        return (await self._profiles.save(params)).model_dump()

    async def delete(self, params: SearchProfileParams) -> dict:
        await self._profiles.delete(params)
        return {"ok": True}
