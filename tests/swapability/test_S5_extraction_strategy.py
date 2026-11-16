"""S5: ToyExtractionStrategy appears in ordered list / provenance field."""

import pytest
from app.application.container import Container
from app.domain.models.context import ExecutionContext
from app.domain.models.extraction import ExtractionTarget
from app.domain.ports.plugins import PluginManifest
from app.plugins.registry import PluginRegistry
from tests.support.fakes import FakeClock, FakeIdGenerator, FakeStructuredLogger
from tests.support.toy_plugins import ToyExtractionStrategy


def _container(registry: PluginRegistry) -> Container:
    return Container(
        config=None,
        clock=FakeClock(),
        ids=FakeIdGenerator(),
        http=None,
        uow_factory=lambda: None,
        events=FakeStructuredLogger(),
        logger=FakeStructuredLogger(),
        registry=registry,
    )


@pytest.mark.asyncio
async def test_toy_extraction_strategy_in_ordered_list():
    registry = PluginRegistry()
    strategy = ToyExtractionStrategy()
    registry.add(
        PluginManifest(
            id="extract.toy",
            kind="extraction",
            version="1.0.0",
            api_version="1",
            capabilities=frozenset({"extract"}),
        ),
        strategy,
    )
    ordered = registry.ordered("extraction")
    assert any(e.manifest.id == "extract.toy" for e in ordered)

    ctx = ExecutionContext(
        task_id="t1", clock=FakeClock(), http=None, logger=FakeStructuredLogger(),
        cancel=None, deadline=None, config={},
    )
    target = ExtractionTarget(url="https://example.com", source_id="s1")
    result = await strategy.extract(target, ctx)
    from app.domain.models.common import Ok
    assert isinstance(result, Ok)
    assert result.value.strategy_id == "extract.toy"