"""discovery.* handlers."""

from __future__ import annotations

from app.application.commands.discovery import DiscoveryUseCase
from app.application.dto import DiscoveryRunParams, EmptyParams


class DiscoveryHandlers:
    def __init__(self, discovery: DiscoveryUseCase) -> None:
        self._discovery = discovery

    async def run(self, params: DiscoveryRunParams) -> dict:
        return await self._discovery.run(params)

    async def list_runs(self, params: EmptyParams) -> dict:
        return (await self._discovery.list_runs()).model_dump()
