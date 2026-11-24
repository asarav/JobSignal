"""system.hello / system.health / system.shutdown handlers."""

from __future__ import annotations

from typing import Any

from app.application.backup import BackupService
from app.application.commands.hello import HealthUseCase, HelloUseCase
from app.application.dto import EmptyParams, HelloParams


class SystemHandlers:
    def __init__(
        self, hello: HelloUseCase, health: HealthUseCase, container: Any = None
    ) -> None:
        self._hello = hello
        self._health = health
        self._container = container

    async def hello(self, params: HelloParams) -> dict:
        return (await self._hello.execute(params)).model_dump()

    async def health(self, params: EmptyParams) -> dict:
        return (await self._health.execute()).model_dump()

    async def shutdown(self, params: EmptyParams) -> dict:
        return {"ok": True}

    async def export_backup(self, params: Any) -> dict:
        return await BackupService(self._container).export(path=params.path)

    async def import_backup(self, params: Any) -> dict:
        return await BackupService(self._container).import_archive(
            path=params.path, confirm=params.confirm
        )

    async def get_backup_history(self, params: Any) -> dict:
        return await BackupService(self._container).history(limit=params.limit)
