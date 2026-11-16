"""Discovery / crawl-run use cases (M1)."""

from __future__ import annotations

from typing import Any, cast

from app.application.container import Container
from app.application.dto import CrawlRunListResult, CrawlRunSummary, DiscoveryRunParams
from app.application.sources import SourceRegistry, run_crawl
from app.domain.models.source import SourceConfig
from app.infrastructure.http.policy_client import PolicyHttpClient


class DiscoveryUseCase:
    def __init__(
        self,
        container: Container,
        registry: SourceRegistry,
        http: PolicyHttpClient,
    ) -> None:
        self._container = container
        self._registry = registry
        self._http = http

    async def run(self, params: DiscoveryRunParams) -> dict[str, object]:
        source = await self._registry_get(params.source_id)
        if source is None:
            raise ValueError(f"unknown source: {params.source_id}")
        connector = self._registry.connector_for(source.connector_id)
        if connector is None:
            raise ValueError(f"no connector for source {source.id}")
        return await run_crawl(
            source=source,
            connector=connector,
            uow_factory=self._container.uow_factory,
            http=self._http,
            clock_now=self._container.clock.now,
            events=self._container.events,
        )

    async def _registry_get(self, source_id: str) -> SourceConfig | None:
        async with self._container.uow_factory() as uow:
            src = await uow.sources.get(source_id)
            await uow.commit()
            return src

    async def list_runs(self, source_id: str | None = None) -> CrawlRunListResult:
        async with self._container.uow_factory() as uow:
            rows = await uow.crawl_runs.list(source_id=source_id)
            await uow.commit()
        return CrawlRunListResult(
            runs=[
                CrawlRunSummary(
                    run_id=str(r.get("id")),
                    source_id=str(r.get("source_id")),
                    status=str(r.get("status")),
                    started_at=cast(str, r.get("started_at")) if isinstance(r.get("started_at"), str) else None,
                    finished_at=cast(str, r.get("finished_at")) if isinstance(r.get("finished_at"), str) else None,
                    stats=cast(dict[str, Any], r.get("stats")) if isinstance(r.get("stats"), dict) else {},
                    error=cast(str, r.get("error")) if (r.get("error") is None or isinstance(r.get("error"), str)) else None,
                )
                for r in rows
            ]
        )
