"""Builtin extraction strategies (M2).

Every strategy is registered as an ``extraction`` plugin, so it shows up in
``plugins.list`` with its health and can be disabled like any other plugin.

The strategies, in pipeline order:

===========================  ======  ==========================================
id                           order   what it reads
===========================  ======  ==========================================
``extract.public_endpoint``   10     a structured JSON payload (ATS API)
``extract.jsonld``            20     schema.org ``JobPosting`` markup
``extract.embedded_json``     30     a job object embedded in the page
``extract.static_html``       40     well-known ATS class names
``extract.generic``           50     Open Graph tags and page heuristics
``extract.partial``          100     last resort: never fails
===========================  ======  ==========================================
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.extraction import ExtractionResult, ExtractionTarget
from app.plugins.extraction.embedded_json import EmbeddedJsonStrategy
from app.plugins.extraction.generic import GenericStrategy
from app.plugins.extraction.jsonld import JsonLdStrategy
from app.plugins.extraction.partial import PartialStrategy
from app.plugins.extraction.public_endpoint import PublicEndpointStrategy
from app.plugins.extraction.rendered_dom import RenderedDomStrategy
from app.plugins.extraction.static_html import StaticHtmlStrategy

if TYPE_CHECKING:
    from app.domain.models.context import ExecutionContext
    from app.plugins.registry import PluginDeps, PluginRegistry

#: Every strategy the pipeline knows, in default order.
STRATEGY_CLASSES = (
    PublicEndpointStrategy,
    JsonLdStrategy,
    EmbeddedJsonStrategy,
    StaticHtmlStrategy,
    RenderedDomStrategy,
    GenericStrategy,
    PartialStrategy,
)


def _make_manifest(strategy_id: str, version: str, description: str):
    from app.domain.ports.plugins import PluginManifest

    return PluginManifest(
        id=strategy_id,
        kind="extraction",
        version=version,
        api_version="1",
        capabilities=frozenset({"extract"}),
        network_hosts=(),
        description=description,
    )


DESCRIPTIONS = {
    "extract.public_endpoint": "Reads fields from a structured JSON job payload.",
    "extract.jsonld": "Reads schema.org JobPosting markup.",
    "extract.embedded_json": "Reads a job object embedded in the page.",
    "extract.static_html": "Reads well-known ATS markup by class name.",
    "extract.rendered_dom": "Renders a JavaScript page and reads its DOM (needs a browser).",
    "extract.generic": "Generic heuristics for unknown career pages.",
    "extract.partial": "Always succeeds and records what little is known.",
}


class DemoExtractionStrategy:
    """Registration smoke test: it performs no real extraction (kept from M0)."""

    id = "extract.demo"
    version = "1.0.0"
    default_order = 900
    excluded_from_pipeline = True

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
            field_confidence={"title": 0.1},
        )
        return Ok(result)


def register(registry: PluginRegistry, deps: PluginDeps) -> None:
    """Register every builtin extraction strategy."""
    for strategy_class in STRATEGY_CLASSES:
        if strategy_class is RenderedDomStrategy:
            strategy = RenderedDomStrategy(browser=deps.browser)
        else:
            strategy = strategy_class()
        registry.add(
            _make_manifest(strategy.id, strategy.version, DESCRIPTIONS[strategy.id]),
            strategy,
        )
    registry.add(
        _make_manifest(
            DemoExtractionStrategy.id,
            DemoExtractionStrategy.version,
            "Builtin demo extraction strategy (registration smoke test).",
        ),
        DemoExtractionStrategy(),
    )
