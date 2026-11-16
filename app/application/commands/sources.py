"""Source management use cases (M1)."""

from __future__ import annotations

from app.application.container import Container
from app.application.dto import (
    SourceAddParams,
    SourceIdParams,
    SourceInfo,
    SourceListResult,
    SourceSetEnabledParams,
)
from app.application.sources import SourceRegistry


class SourcesUseCase:
    def __init__(self, container: Container, registry: SourceRegistry) -> None:
        self._container = container
        self._registry = registry

    async def list(self) -> SourceListResult:
        sources = await self._registry.list()
        return SourceListResult(
            sources=[
                SourceInfo(
                    source_id=s.id,
                    url=s.url,
                    kind=s.kind,
                    connector_id=s.connector_id,
                    company_id=s.company_id,
                    enabled=s.enabled,
                )
                for s in sources
            ]
        )

    async def add(self, params: SourceAddParams) -> SourceInfo:
        source = await self._registry.add(params.url, connector_id=params.connector_id, company_id=params.company_id)
        return SourceInfo(
            source_id=source.id,
            url=source.url,
            kind=source.kind,
            connector_id=source.connector_id,
            company_id=source.company_id,
            enabled=source.enabled,
        )

    async def set_enabled(self, params: SourceSetEnabledParams) -> None:
        await self._registry.set_enabled(params.source_id, params.enabled)

    async def delete(self, params: SourceIdParams) -> None:
        await self._registry.delete(params.source_id)
