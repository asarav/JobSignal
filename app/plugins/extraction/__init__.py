"""Builtin demo extraction strategy (M0 registry smoke test).

This is the single builtin plugin shipped in M0.  It is intentionally trivial:
it demonstrates the plugin registration pattern and gives ``plugins.list`` a
real entry to assert against.  It performs no real extraction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.extraction import ExtractionResult, ExtractionTarget

if TYPE_CHECKING:
    from app.domain.models.context import ExecutionContext
    from app.plugins.registry import PluginDeps, PluginRegistry


def _make_manifest():
    from app.domain.ports.plugins import PluginManifest

    return PluginManifest(
        id="extract.demo",
        kind="extraction",
        version="1.0.0",
        api_version="1",
        capabilities=frozenset({"extract"}),
        network_hosts=(),
        description="Builtin demo extraction strategy (M0 smoke test; no real extraction).",
    )


class DemoExtractionStrategy:
    id = "extract.demo"
    version = "1.0.0"
    default_order = 5

    def can_attempt(self, target: ExtractionTarget) -> bool:
        return True

    async def extract(
        self, target: ExtractionTarget, ctx: ExecutionContext
    ) -> Result[ExtractionResult, PortError]:
        from app.domain.models.job import RawJob, RawJobRef

        ref = RawJobRef(source_id=target.source_id, url=target.url)
        raw = RawJob(ref=ref, title="demo", body_text=None, body_html=None)
        result = ExtractionResult(
            raw=raw,
            fields_found=("title",),
            confidence=0.1,
            strategy_id=self.id,
            plugin_version=self.version,
        )
        return Ok(result)


def register(registry: PluginRegistry, deps: PluginDeps) -> None:
    registry.add(_make_manifest(), DemoExtractionStrategy())