"""Source management use cases (M1)."""

from __future__ import annotations

from app.application.container import Container
from app.application.dto import (
    SourceAddParams,
    SourceIdParams,
    SourceInfo,
    SourceListResult,
    SourceSetEnabledParams,
    SourceSetExtractionOrderParams,
    SourceSetScheduleParams,
)
from app.application.sources import SourceRegistry
from app.domain.models.source import SourceConfig

#: Rows the Sources screen receives.  Discovery can register the whole seed
#: catalogue, and rendering or transferring every row would make the app slow;
#: the rest stay active and are reported through ``total``/``truncated``.
SOURCE_LIST_LIMIT = 500


class SourcesUseCase:
    def __init__(self, container: Container, registry: SourceRegistry) -> None:
        self._container = container
        self._registry = registry

    async def list(self) -> SourceListResult:
        sources = await self._registry.list_sources()
        # Boards the user added stay visible; discovered seed boards fill the
        # rest of the page.  Both sets remain enabled and keep being crawled.
        manual = [s for s in sources if "probe_status" not in s.schedule]
        discovered = [s for s in sources if "probe_status" in s.schedule]
        selected = (manual + discovered)[:SOURCE_LIST_LIMIT]
        return SourceListResult(
            sources=[_to_info(s) for s in selected],
            total=len(sources),
            truncated=len(selected) < len(sources),
        )

    async def add(self, params: SourceAddParams) -> SourceInfo:
        source = await self._registry.add(
            params.url,
            connector_id=params.connector_id,
            company_id=params.company_id,
            interval_s=params.interval_s,
        )
        return _to_info(source)

    async def set_enabled(self, params: SourceSetEnabledParams) -> None:
        await self._registry.set_enabled(params.source_id, params.enabled)

    async def set_extraction_order(self, params: SourceSetExtractionOrderParams) -> None:
        await self._registry.set_extraction_order(params.source_id, params.order)

    async def set_schedule(self, params: SourceSetScheduleParams) -> None:
        """Change how often the source is crawled (0 disables scheduling)."""
        await self._registry.set_schedule(params.source_id, params.interval_s)

    async def delete(self, params: SourceIdParams) -> None:
        await self._registry.delete(params.source_id)


def _to_info(source: SourceConfig) -> SourceInfo:
    """Map a stored source to the DTO the UI consumes."""
    raw_interval = source.schedule.get("interval_s", 0)
    interval = float(raw_interval) if isinstance(raw_interval, (int, float)) else 0.0
    raw_tier = source.schedule.get("tier")
    raw_reason = source.schedule.get("tier_reason")
    return SourceInfo(
        source_id=source.id,
        url=source.url,
        kind=source.kind,
        connector_id=source.connector_id,
        company_id=source.company_id,
        enabled=source.enabled,
        interval_s=interval,
        extraction_order=list(source.extraction_order),
        tier=raw_tier if isinstance(raw_tier, str) else None,
        tier_reason=raw_reason if isinstance(raw_reason, str) else None,
    )
