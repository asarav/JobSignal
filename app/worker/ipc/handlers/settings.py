"""settings.get / settings.update handlers."""

from __future__ import annotations

from app.application.commands.settings import SettingsUseCase
from app.application.dto import EmptyParams, SettingsPatch


class SettingsHandlers:
    def __init__(self, settings: SettingsUseCase) -> None:
        self._settings = settings

    async def get(self, params: EmptyParams) -> dict:
        return (await self._settings.get()).model_dump()

    async def update(self, params: SettingsPatch) -> dict:
        return (await self._settings.update(params)).model_dump()
