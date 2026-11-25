"""discovery.* handlers."""

from __future__ import annotations

from app.application.auto_discover import AutoDiscoverUseCase
from app.application.commands.discovery import DiscoveryUseCase
from app.application.dto import CrawlRunListParams, DiscoveryRunParams, EmptyParams


class DiscoveryHandlers:
    def __init__(
        self,
        discovery: DiscoveryUseCase,
        auto_discover: AutoDiscoverUseCase,
    ) -> None:
        self._discovery = discovery
        self._auto_discover = auto_discover

    async def run(self, params: DiscoveryRunParams) -> dict:
        return await self._discovery.run(params)

    async def list_runs(self, params: CrawlRunListParams) -> dict:
        return (
            await self._discovery.list_runs(params.source_id, limit=params.limit)
        ).model_dump()

    async def auto_discover(self, params: EmptyParams) -> dict:
        return (await self._auto_discover.run()).model_dump()
