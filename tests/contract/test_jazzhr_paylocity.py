"""Contract: JazzHR and Paylocity boards."""

from __future__ import annotations

import pytest
from app.domain.models.common import Err, Ok, PortError
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.http import HttpResponse
from app.domain.models.job import RawJobRef
from app.domain.models.source import SourceConfig
from app.domain.services.classifier import KNOWN_PATTERNS
from app.plugins.ats.jazzhr import JazzHrConnector
from app.plugins.ats.paylocity import PaylocityConnector, board_url, company_id
from tests.support.fakes import FakeClock, FakeHttpClient, FakeStructuredLogger


def _ctx(http: FakeHttpClient) -> ExecutionContext:
    return ExecutionContext(
        task_id="contract",
        clock=FakeClock(),
        http=http,
        logger=FakeStructuredLogger(),
        cancel=CancellationToken(),
        deadline=None,
        config={},
    )


def _html_response(url: str, html: str, status: int = 200) -> HttpResponse:
    return HttpResponse(
        status=status,
        url=url,
        headers={"content-type": "text/html"},
        body=html.encode(),
        elapsed_ms=5,
    )


JAZZHR_SOURCE = "https://acme.applytojob.com/apply"
JAZZHR_LIST = "https://acme.applytojob.com/apply/jobs"
JAZZHR_HTML = """
<a href="/apply/jobs/details/AbC123">Site Reliability Engineer</a>
<a href="/apply/jobs/details/AbC123">Site Reliability Engineer</a>
<a href="/apply/jobs/details/Zz9">Designer</a>
"""

PAYLOCITY_COMPANY = "142c9c7e-4192-4bef-9adf-86db6d8f712c"
PAYLOCITY_SOURCE = (
    f"https://recruiting.paylocity.com/Recruiting/Jobs/All/{PAYLOCITY_COMPANY}/LayerCake"
)
PAYLOCITY_BOARD = f"https://recruiting.paylocity.com/Recruiting/Jobs/All/{PAYLOCITY_COMPANY}"
PAYLOCITY_HTML = """
<a href="/recruiting/jobs/details/1139001/Data-Engineer">Data Engineer</a>
<a href="/Recruiting/Jobs/Details/1139001/Data-Engineer">Data Engineer</a>
<a href="/Recruiting/Jobs/Details/1139002">Analyst</a>
"""


@pytest.mark.asyncio
async def test_jazzhr_lists_each_posting_once():
    http = FakeHttpClient({JAZZHR_LIST: _html_response(JAZZHR_LIST, JAZZHR_HTML)})
    connector = JazzHrConnector()
    source = SourceConfig(id="s1", url=JAZZHR_SOURCE, kind="ats", connector_id="ats.jazzhr")

    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]

    assert [ref.url for ref in refs] == [
        "https://acme.applytojob.com/apply/jobs/details/AbC123",
        "https://acme.applytojob.com/apply/jobs/details/Zz9",
    ]
    assert [ref.source_posting_id for ref in refs] == ["AbC123", "Zz9"]

    result = await connector.fetch_job(refs[0], source, _ctx(http))
    assert isinstance(result, Ok)
    assert result.value.body_html is not None


@pytest.mark.asyncio
async def test_jazzhr_rejects_a_tenant_that_redirects_to_the_platform():
    # An unknown tenant is answered by JazzHR's own marketing site, with a 200.
    redirected = _html_response("https://www.jazzhr.com/", "<html>Hire better.</html>")
    http = FakeHttpClient({JAZZHR_LIST: redirected})
    source = SourceConfig(id="s1", url=JAZZHR_SOURCE, kind="ats", connector_id="ats.jazzhr")

    with pytest.raises(PortError) as raised:
        [ref async for ref in await JazzHrConnector().list_jobs(source, _ctx(http))]

    assert raised.value.retryable is False


def test_jazzhr_matches_only_tenant_subdomains():
    connector = JazzHrConnector()

    assert connector.matches("https://acme.applytojob.com/apply/jobs").matched
    assert not connector.matches("https://www.jazzhr.com/careers").matched
    assert not connector.matches("https://applytojob.com/apply").matched


@pytest.mark.asyncio
async def test_paylocity_lists_canonical_postings_from_a_slugged_board():
    http = FakeHttpClient({PAYLOCITY_BOARD: _html_response(PAYLOCITY_BOARD, PAYLOCITY_HTML)})
    connector = PaylocityConnector()
    source = SourceConfig(
        id="s1",
        url=PAYLOCITY_SOURCE,
        kind="ats",
        connector_id="ats.paylocity",
        ats_tenant_key=PAYLOCITY_COMPANY,
    )

    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]

    assert [ref.url for ref in refs] == [
        "https://recruiting.paylocity.com/Recruiting/Jobs/Details/1139001",
        "https://recruiting.paylocity.com/Recruiting/Jobs/Details/1139002",
    ]


def test_paylocity_reads_the_company_id_and_drops_the_slug():
    upper = PAYLOCITY_SOURCE.replace("recruiting/jobs/All", "recruiting/jobs/all").upper()

    assert company_id(PAYLOCITY_SOURCE) == PAYLOCITY_COMPANY
    assert company_id(upper) == PAYLOCITY_COMPANY
    assert company_id("https://recruiting.paylocity.com/Recruiting/Jobs/All") is None
    assert company_id("https://recruiting.paylocity.com/Recruiting/Jobs/Details/1139001") is None
    assert board_url(PAYLOCITY_SOURCE) == PAYLOCITY_BOARD
    assert board_url("https://example.com/Recruiting/Jobs/All/abc") is None


def test_the_classifier_routes_both_platforms():
    patterns = dict(KNOWN_PATTERNS)

    assert patterns["applytojob.com"] == "ats.jazzhr"
    assert patterns["recruiting.paylocity.com/recruiting/jobs/all/"] == "ats.paylocity"


@pytest.mark.asyncio
async def test_jazzhr_returns_a_posting_page():
    detail = "https://acme.applytojob.com/apply/jobs/details/AbC123"
    http = FakeHttpClient(
        {
            detail: _html_response(
                detail,
                "<html><body><h1>Site Reliability Engineer</h1>"
                "<p>Own the on-call rotation.</p></body></html>",
            )
        }
    )
    source = SourceConfig(id="s1", url=JAZZHR_SOURCE, kind="ats", connector_id="ats.jazzhr")
    ref = RawJobRef(source_id="s1", url=detail, source_posting_id="AbC123")

    result = await JazzHrConnector().fetch_job(ref, source, _ctx(http))

    assert isinstance(result, Ok)
    assert "Site Reliability Engineer" in (result.value.body_html or "")


@pytest.mark.asyncio
async def test_a_retired_paylocity_posting_fails_clearly():
    """Measured: a retired company answers 200 with a not-found page.

    Returning it as a posting would persist a job with no title and no
    description, which looks like a healthy crawl that found nothing.
    """
    detail = "https://recruiting.paylocity.com/Recruiting/Jobs/Details/1139001"
    http = FakeHttpClient(
        {
            detail: _html_response(
                detail,
                "<html><body><h1>JobNotFound</h1>"
                "<p>This job is no longer available.</p></body></html>",
            )
        }
    )
    source = SourceConfig(
        id="s1",
        url=PAYLOCITY_SOURCE,
        kind="ats",
        connector_id="ats.paylocity",
        ats_tenant_key=PAYLOCITY_COMPANY,
    )
    ref = RawJobRef(source_id="s1", url=detail, source_posting_id="1139001")

    result = await PaylocityConnector().fetch_job(ref, source, _ctx(http))

    assert isinstance(result, Err)
    assert result.error.code == "connector.invalid_response"
    assert result.error.retryable is False
    assert "jobnotfound" in str(result.error.details["reason"]).lower()
