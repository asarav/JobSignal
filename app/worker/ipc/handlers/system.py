"""system.hello / system.health / system.shutdown handlers."""

from __future__ import annotations

from app.application.commands.hello import HealthUseCase, HelloUseCase
from app.application.dto import EmptyParams, HelloParams


class SystemHandlers:
    def __init__(self, hello: HelloUseCase, health: HealthUseCase) -> None:
        self._hello = hello
        self._health = health

    async def hello(self, params: HelloParams) -> dict:
        return (await self._hello.execute(params)).model_dump()

    async def health(self, params: EmptyParams) -> dict:
        return (await self._health.execute()).model_dump()

    async def shutdown(self, params: EmptyParams) -> dict:
        return {"ok": True}
