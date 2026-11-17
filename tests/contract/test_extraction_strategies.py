"""Contract: every extraction strategy obeys the same contract (M2).

The shared contract is what makes the pipeline possible: a strategy declares
what it can see, returns the fields it found, and reports its confidence per
field. A strategy that cannot find anything returns an error instead of an
empty result.
"""

from __future__ import annotations

import pytest
from app.domain.models.common import Err, Ok
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.extraction import ExtractionTarget
from app.plugins.extraction import STRATEGY_CLASSES
from app.plugins.extraction.base import StrategyBase
from app.plugins.extraction.embedded_json import EmbeddedJsonStrategy
from app.plugins.extraction.generic import GenericStrategy
from app.plugins.extraction.jsonld import JsonLdStrategy
from app.plugins.extraction.partial import PartialStrategy
from app.plugins.extraction.public_endpoint import PublicEndpointStrategy
from app.plugins.extraction.static_html import StaticHtmlStrategy
from tests.support.fakes import FakeBrowserProvider, FakeClock, FakeStructuredLogger

HTML_WITH_JSONLD = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting",
 "title":"Senior Data Engineer",
 "description":"<p>Own the data platform.</p>",
 "employmentType":"FULL_TIME",
 "datePosted":"2026-01-05",
 "hiringOrganization":{"@type":"Organization","name":"Acme Data"},
 "jobLocation":{"@type":"Place","address":{"addressLocality":"Berlin","addressCountry":"DE"}}}
</script>
</head><body><h1>Senior Data Engineer</h1></body></html>
"""

HTML_WITH_EMBEDDED_JSON = """
<html><body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"job":{"title":"Platform Engineer",
 "description":"Build internal platforms.",
 "location":"Remote (EU)","employmentType":"full_time",
 "postedAt":"2026-02-01","companyName":"Acme"}}}}
</script>
</body></html>
"""

HTML_WITH_ATS_CLASSES = """
<html><body>
<h1>Backend Engineer</h1>
<div class="job-location">Amsterdam, Netherlands</div>
<div class="employment-type">Full-time</div>
<div class="job-description">You will build and operate our backend services.</div>
</body></html>
"""

HTML_GENERIC = """
<html><head>
<meta property="og:title" content="Growth Engineer">
<meta property="og:description" content="We are looking for a growth engineer to join the team.">
</head><body><h1>Growth Engineer</h1>
<p>You will own our growth experiments across all channels and report on them weekly.</p>
</body></html>
"""

HTML_RENDERED = """
<html><body>
  <h1>Frontend Engineer</h1>
  <div class="job-description">Build the user-facing web application.</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_rendered_dom_reads_a_rendered_page():
    from app.plugins.extraction.rendered_dom import RenderedDomStrategy

    provider = FakeBrowserProvider(html=HTML_RENDERED)
    strategy = RenderedDomStrategy(browser=provider)
    target = _target(url="https://example.com/jobs/42")
    assert strategy.can_attempt(target) is True

    result = await strategy.extract(target, _ctx())
    assert isinstance(result, Ok)
    assert result.value.raw.title == "Frontend Engineer"
    assert "user-facing" in (result.value.raw.body_text or "")


@pytest.mark.asyncio
async def test_rendered_dom_refuses_a_challenge_page():
    from app.plugins.extraction.rendered_dom import RenderedDomStrategy

    provider = FakeBrowserProvider(html="<html><body>Just a moment... verifying you are human</body></html>")
    strategy = RenderedDomStrategy(browser=provider)
    result = await strategy.extract(_target(url="https://example.com/jobs/42"), _ctx())
    assert isinstance(result, Err)
    assert result.error.code == "rendered_dom.challenge_detected"


@pytest.mark.asyncio
async def test_rendered_dom_is_skipped_without_a_browser():
    from app.plugins.extraction.rendered_dom import RenderedDomStrategy

    strategy = RenderedDomStrategy(browser=None)
    assert strategy.can_attempt(_target(url="https://example.com/jobs/42")) is False


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        task_id="contract",
        clock=FakeClock(),
        http=None,
        logger=FakeStructuredLogger(),
        cancel=CancellationToken(),
        deadline=None,
        config={},
    )


def _target(**kwargs) -> ExtractionTarget:
    payload = {"url": "https://example.com/jobs/1", "source_id": "s1"}
    payload.update(kwargs)
    return ExtractionTarget(**payload)


@pytest.mark.parametrize("strategy_class", STRATEGY_CLASSES)
def test_strategy_declares_its_identity(strategy_class):
    strategy = strategy_class()
    assert isinstance(strategy, StrategyBase)
    assert strategy.id.startswith("extract.")
    assert strategy.version
    assert isinstance(strategy.default_order, int)


@pytest.mark.parametrize("strategy_class", STRATEGY_CLASSES)
@pytest.mark.asyncio
async def test_strategy_returns_ok_or_err(strategy_class):
    strategy = strategy_class()
    outcome = await strategy.extract(_target(html=HTML_WITH_JSONLD), _ctx())
    assert isinstance(outcome, (Ok, Err))


@pytest.mark.asyncio
async def test_public_endpoint_reads_structured_payload():
    strategy = PublicEndpointStrategy()
    target = _target(json_payload={"title": "Data Engineer", "content": "Build models."})
    assert strategy.can_attempt(target) is True

    result = await strategy.extract(target, _ctx())
    assert isinstance(result, Ok)
    assert result.value.raw.title == "Data Engineer"
    assert result.value.raw.body_text == "Build models."
    assert "title" in result.value.fields_found


@pytest.mark.asyncio
async def test_public_endpoint_is_skipped_without_payload():
    assert PublicEndpointStrategy().can_attempt(_target(html=HTML_GENERIC)) is False


@pytest.mark.asyncio
async def test_jsonld_reads_job_posting():
    strategy = JsonLdStrategy()
    target = _target(html=HTML_WITH_JSONLD)
    assert strategy.can_attempt(target) is True

    result = await strategy.extract(target, _ctx())
    assert isinstance(result, Ok)
    assert result.value.raw.title == "Senior Data Engineer"
    assert "Own the data platform." in (result.value.raw.body_text or "")
    assert result.value.raw.payload["company_name"] == "Acme Data"
    assert result.value.raw.payload["locations"] == ["Berlin, DE"]
    assert result.value.field_confidence["title"] >= 0.9


@pytest.mark.asyncio
async def test_jsonld_without_markup_reports_no_fields():
    strategy = JsonLdStrategy()
    result = await strategy.extract(_target(html="<html><body>nothing</body></html>"), _ctx())
    assert isinstance(result, Err)
    assert result.error.code == "extraction.no_fields"


@pytest.mark.asyncio
async def test_embedded_json_finds_the_job_object():
    strategy = EmbeddedJsonStrategy()
    target = _target(html=HTML_WITH_EMBEDDED_JSON)
    assert strategy.can_attempt(target) is True

    result = await strategy.extract(target, _ctx())
    assert isinstance(result, Ok)
    assert result.value.raw.title == "Platform Engineer"
    assert result.value.raw.body_text == "Build internal platforms."


@pytest.mark.asyncio
async def test_static_html_reads_known_class_names():
    strategy = StaticHtmlStrategy()
    result = await strategy.extract(_target(html=HTML_WITH_ATS_CLASSES), _ctx())
    assert isinstance(result, Ok)
    assert result.value.raw.title == "Backend Engineer"
    assert "backend services" in (result.value.raw.body_text or "")
    assert result.value.raw.payload["locations"] == ["Amsterdam, Netherlands"]
    assert result.value.raw.payload["employment_type"] == "Full-time"


@pytest.mark.asyncio
async def test_generic_uses_metadata_and_longest_block():
    strategy = GenericStrategy()
    result = await strategy.extract(_target(html=HTML_GENERIC), _ctx())
    assert isinstance(result, Ok)
    assert result.value.raw.title == "Growth Engineer"
    assert result.value.raw.body_text
    # The generic strategy must stay less confident than the structured ones.
    assert result.value.field_confidence["title"] < 0.5


@pytest.mark.asyncio
async def test_partial_never_fails_and_uses_the_url():
    strategy = PartialStrategy()
    target = _target(url="https://example.com/jobs/backend-engineer")
    result = await strategy.extract(target, _ctx())
    assert isinstance(result, Ok)
    assert result.value.raw.title == "Backend Engineer"
    assert result.value.field_confidence["title"] < 0.2
