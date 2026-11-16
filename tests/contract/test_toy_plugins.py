"""Contract: toy plugins obey the shape of their ports (no real crawling).

These tests prove the registration contract is enough to add a connector,
signal provider, scoring model, or extraction strategy without touching core
application code.
"""

from __future__ import annotations

import pytest
from app.domain.models.browser import OpenOptions
from app.domain.models.candidate import CandidateProfile
from app.domain.models.common import Ok
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.extraction import ExtractionTarget
from app.domain.models.job import JobView
from app.domain.models.matching import MatchContext, ScoringConfig
from app.domain.models.posting import JobHistory, SignalContext
from app.domain.models.source import SourceConfig
from tests.support.fakes import FakeBrowserProvider, FakeClock
from tests.support.toy_plugins import (
    ConstantScoringModel,
    ToyAtsConnector,
    ToyExtractionStrategy,
    ToySignalProvider,
)


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        task_id="contract",
        clock=FakeClock(),
        http=None,
        logger=None,
        cancel=CancellationToken(),
        deadline=None,
        config={},
    )


@pytest.mark.asyncio
async def test_toy_connector_shape():
    connector = ToyAtsConnector()
    assert connector.matches("https://toy.example.com/jobs").matched is True
    assert connector.matches("https://other.example.com").matched is False

    source = SourceConfig(id="s1", url="https://toy.example.com/jobs", kind="ats")
    refs = [ref async for ref in await connector.list_jobs(source, _ctx())]
    assert len(refs) == 1

    result = await connector.fetch_job(refs[0], _ctx())
    assert isinstance(result, Ok)
    assert result.value.title == "Toy Job"


@pytest.mark.asyncio
async def test_toy_extraction_shape():
    strategy = ToyExtractionStrategy()
    target = ExtractionTarget(url="https://example.com", source_id="s1")
    assert strategy.can_attempt(target) is True
    result = await strategy.extract(target, _ctx())
    assert isinstance(result, Ok)
    assert result.value.strategy_id == "extract.toy"


def test_toy_signal_provider_shape():
    result = ToySignalProvider().evaluate(
        JobHistory(job_id="j1"), SignalContext(job_id="j1")
    )
    assert result.signal_id == "signal.toy"
    assert result.status == "available"


@pytest.mark.asyncio
async def test_constant_scoring_model_shape():
    model = ConstantScoringModel(score=0.61)
    profile = CandidateProfile(id="p1", display_name="Test")
    job = JobView(
        id="j1",
        title="T",
        company_id="c1",
        company_name="C",
        canonical_url="https://example.com/j1",
        description_text="",
        workplace_type="remote",
        employment_type="full_time",
        seniority="mid",
    )
    cfg = ScoringConfig(model_id="match.constant", model_version="1.0.0", weights={})
    result = await model.evaluate(profile, job, MatchContext(), [], cfg)
    assert result.fit_score == 0.61
    assert result.model_id == "match.constant"


@pytest.mark.asyncio
async def test_fake_browser_provider_shape():
    provider = FakeBrowserProvider()
    result = await provider.open("https://example.com/apply", OpenOptions())
    assert isinstance(result, Ok)
    assert result.value.url == "https://example.com/apply"
    await provider.close_all()
