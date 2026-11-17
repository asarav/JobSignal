"""Extraction pipeline: ordering, per-source override, and field-level merge."""

from __future__ import annotations

import pytest
from app.application.extraction import ExtractionPipeline
from app.domain.models.common import Ok, PortError
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.extraction import ExtractionResult, ExtractionTarget
from app.domain.models.job import RawJob, RawJobRef
from app.domain.services.merge import merge_results
from tests.support.fakes import FakeClock, FakeStructuredLogger


class _Strategy:
    """A strategy stub that returns a fixed set of fields."""

    def __init__(self, strategy_id: str, order: int, fields: dict, confidence: float) -> None:
        self.id = strategy_id
        self.version = "1.0.0"
        self.default_order = order
        self._fields = fields
        self._confidence = confidence
        self.calls = 0

    def can_attempt(self, target: ExtractionTarget) -> bool:
        return True

    async def extract(self, target: ExtractionTarget, ctx: ExecutionContext):
        self.calls += 1
        raw = RawJob(
            ref=RawJobRef(source_id=target.source_id, url=target.url),
            title=self._fields.get("title"),
            body_text=self._fields.get("description_text"),
            body_html=None,
            payload=dict(self._fields),
        )
        return Ok(
            ExtractionResult(
                raw=raw,
                fields_found=tuple(self._fields),
                confidence=self._confidence,
                strategy_id=self.id,
                plugin_version="1.0.0",
                field_confidence={name: self._confidence for name in self._fields},
            )
        )


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        task_id="test",
        clock=FakeClock(),
        http=None,
        logger=FakeStructuredLogger(),
        cancel=CancellationToken(),
        deadline=None,
        config={},
    )


def _target() -> ExtractionTarget:
    return ExtractionTarget(url="https://example.com/jobs/1", source_id="s1", html="<html/>")


@pytest.mark.asyncio
async def test_pipeline_runs_strategies_in_default_order():
    low = _Strategy("extract.low", 50, {"title": "Low"}, 0.4)
    high = _Strategy("extract.high", 10, {"title": "High"}, 0.9)
    pipeline = ExtractionPipeline([low, high])

    merged = await pipeline.extract(_target(), _ctx())

    assert merged.value("title") == "High"
    assert merged.strategies_used == ("extract.high", "extract.low")


@pytest.mark.asyncio
async def test_source_override_moves_a_strategy_to_the_front():
    low = _Strategy("extract.low", 50, {"title": "Low"}, 0.4)
    high = _Strategy("extract.high", 10, {"title": "High"}, 0.9)
    pipeline = ExtractionPipeline([low, high])

    merged = await pipeline.extract(_target(), _ctx(), order=["extract.low"])

    # The override decides who runs first, but the merge still trusts the
    # higher confidence.
    assert merged.strategies_used[0] == "extract.low"
    assert merged.value("title") == "High"


@pytest.mark.asyncio
async def test_merge_keeps_the_most_confident_value_per_field():
    title = _Strategy("extract.title", 10, {"title": "Structured title"}, 0.95)
    description = _Strategy("extract.desc", 20, {"description_text": "Long text"}, 0.8)
    pipeline = ExtractionPipeline([title, description])

    merged = await pipeline.extract(_target(), _ctx())

    assert merged.value("title") == "Structured title"
    assert merged.value("description_text") == "Long text"
    assert merged.confidence("title") == 0.95
    assert merged.provenance()["title"] == "extract.title"


def test_merge_keeps_the_first_strategy_on_a_tie():
    def result(strategy_id: str, title: str) -> ExtractionResult:
        return ExtractionResult(
            raw=RawJob(
                ref=RawJobRef(source_id="s1", url="https://example.com/1"),
                title=title,
                body_text=None,
                body_html=None,
                payload={"title": title},
            ),
            fields_found=("title",),
            confidence=0.5,
            strategy_id=strategy_id,
            plugin_version="1.0.0",
            field_confidence={"title": 0.5},
        )

    merged = merge_results(
        [result("extract.first", "First"), result("extract.second", "Second")]
    )

    assert merged.value("title") == "First"
    assert merged.provenance()["title"] == "extract.first"


def test_merge_reports_completeness_and_status():
    result = ExtractionResult(
        raw=RawJob(
            ref=RawJobRef(source_id="s1", url="https://example.com/1"),
            title="Engineer",
            body_text="Body",
            body_html=None,
            payload={"title": "Engineer", "description_text": "Body"},
        ),
        fields_found=("title", "description_text"),
        confidence=0.8,
        strategy_id="extract.test",
        plugin_version="1.0.0",
        field_confidence={"title": 0.8, "description_text": 0.8},
    )
    merged = merge_results([result])
    assert merged.completeness == 0.5
    assert merged.status.value == "partial"


@pytest.mark.asyncio
async def test_pipeline_skips_strategies_that_cannot_attempt():
    class _Skipping(_Strategy):
        def can_attempt(self, target: ExtractionTarget) -> bool:
            return False

    skipping = _Skipping("extract.skip", 10, {"title": "Nope"}, 0.9)
    used = _Strategy("extract.used", 20, {"title": "Yes"}, 0.5)
    pipeline = ExtractionPipeline([skipping, used])

    merged = await pipeline.extract(_target(), _ctx())

    assert skipping.calls == 0
    assert used.calls == 1
    assert merged.value("title") == "Yes"


@pytest.mark.asyncio
async def test_pipeline_survives_a_failing_strategy():
    class _Failing(_Strategy):
        async def extract(self, target: ExtractionTarget, ctx: ExecutionContext):
            raise PortError(code="extraction.failed", message="boom")

    failing = _Failing("extract.broken", 10, {"title": "Nope"}, 0.9)
    working = _Strategy("extract.working", 20, {"title": "Yes"}, 0.5)
    pipeline = ExtractionPipeline([failing, working])

    # A broken strategy must not abort the pipeline.
    merged = await pipeline.extract(_target(), _ctx())

    assert working.calls == 1
    assert merged.value("title") == "Yes"
