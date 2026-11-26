"""Contract: the Rippling, Personio and Breezy HR connectors."""

from __future__ import annotations

import json

import pytest
from app.domain.models.common import Ok, PortError
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.http import HttpResponse
from app.domain.models.source import SourceConfig
from app.plugins.ats.breezy import BreezyConnector
from app.plugins.ats.personio import PersonioConnector
from app.plugins.ats.rippling import RipplingConnector
from tests.support.fakes import FakeClock, FakeHttpClient, FakeStructuredLogger

PERSONIO_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position>
    <id>2874840</id>
    <office>Berlin</office>
    <department>Regionen</department>
    <name>General Manager</name>
    <jobDescriptions>
      <jobDescription>
        <name>Mission</name>
        <value><![CDATA[<p>Run the region</p>]]></value>
      </jobDescription>
    </jobDescriptions>
    <employmentType>permanent</employmentType>
    <createdAt>2026-05-01</createdAt>
  </position>
</workzag-jobs>
"""


def _ctx(http: FakeHttpClient) -> ExecutionContext:
    return ExecutionContext(
        task_id="contract", clock=FakeClock(), http=http, logger=FakeStructuredLogger(),
        cancel=CancellationToken(), deadline=None, config={},
    )


def _json_response(url: str, payload: object) -> HttpResponse:
    return HttpResponse(
        status=200,
        url=url,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode(),
        elapsed_ms=5,
    )


def _html_response(url: str, html: str) -> HttpResponse:
    return HttpResponse(
        status=200,
        url=url,
        headers={"content-type": "text/html"},
        body=html.encode(),
        elapsed_ms=5,
    )


@pytest.mark.asyncio
async def test_rippling_pages_the_board_api_and_reads_the_next_payload():
    feed = "https://ats.rippling.com/api/v2/board/acme/jobs?page=1"
    detail = "https://ats.rippling.com/acme/jobs/j1"
    next_data = {
        "props": {
            "pageProps": {
                "apiData": {
                    "jobPost": {
                        "name": "Staff Engineer",
                        "description": {
                            "role": "<p>Build the platform</p>",
                            "company": "<p>We ship</p>",
                        },
                        "locations": [{"name": "Remote"}],
                    }
                }
            }
        }
    }
    html = (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(next_data)}</script></body></html>"
    )
    http = FakeHttpClient(
        {
            feed: _json_response(
                feed,
                {
                    "items": [{"id": "j1", "name": "Staff Engineer", "url": detail}],
                    "page": 1,
                    "pageSize": 20,
                    "totalItems": 1,
                    "totalPages": 1,
                },
            ),
            detail: _html_response(detail, html),
        }
    )
    connector = RipplingConnector()
    assert connector.matches("https://ats.rippling.com/acme/jobs").matched is True
    assert connector.matches("https://ats.rippling.com/api/v2/board/acme/jobs").matched is False
    assert connector.matches("https://www.rippling.com/pricing").matched is False

    source = SourceConfig(
        id="s1", url="https://ats.rippling.com/acme/jobs", kind="ats",
        connector_id="ats.rippling",
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert [ref.source_posting_id for ref in refs] == ["j1"]

    result = await connector.fetch_job(refs[0], source, _ctx(http))
    assert isinstance(result, Ok)
    assert result.value.title == "Staff Engineer"
    assert result.value.body_text == "Build the platform We ship"
    assert result.value.payload["locations"] == [{"name": "Remote"}]


@pytest.mark.asyncio
async def test_rippling_stops_paging_when_the_board_reports_one_page():
    first = "https://ats.rippling.com/api/v2/board/acme/jobs?page=1"
    http = FakeHttpClient(
        {first: _json_response(first, {"items": [], "totalItems": 0, "totalPages": 1})}
    )
    source = SourceConfig(
        id="s1", url="https://ats.rippling.com/acme/jobs", kind="ats",
        connector_id="ats.rippling",
    )

    refs = [ref async for ref in await RipplingConnector().list_jobs(source, _ctx(http))]

    assert refs == []
    assert len(http.sent) == 1


@pytest.mark.asyncio
async def test_personio_lists_and_fetches_from_the_xml_feed():
    host = "acme.jobs.personio.de"
    feed = f"https://{host}/xml"
    http = FakeHttpClient(
        {
            feed: HttpResponse(
                status=200, url=feed, headers={"content-type": "text/xml"},
                body=PERSONIO_XML, elapsed_ms=5,
            )
        }
    )
    connector = PersonioConnector()
    assert connector.matches(f"https://{host}/").matched is True
    assert connector.matches("https://acme.jobs.personio.com/jobs").matched is True
    assert connector.matches("https://www.personio.de/").matched is False

    source = SourceConfig(id="s1", url=f"https://{host}/", kind="ats", connector_id="ats.personio")
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert [(ref.source_posting_id, ref.url) for ref in refs] == [
        ("2874840", f"https://{host}/job/2874840")
    ]

    result = await connector.fetch_job(refs[0], source, _ctx(http))
    assert isinstance(result, Ok)
    assert result.value.title == "General Manager"
    assert result.value.body_html == "<p>Run the region</p>"
    assert result.value.payload["office"] == "Berlin"
    assert result.value.payload["employmentType"] == "permanent"


@pytest.mark.asyncio
async def test_personio_rejects_a_document_that_is_not_its_feed():
    feed = "https://acme.jobs.personio.de/xml"
    http = FakeHttpClient(
        {feed: _html_response(feed, "<html><body>not a feed</body></html>")}
    )
    source = SourceConfig(
        id="s1", url="https://acme.jobs.personio.de/", kind="ats", connector_id="ats.personio"
    )

    with pytest.raises(PortError):
        [ref async for ref in await PersonioConnector().list_jobs(source, _ctx(http))]


@pytest.mark.asyncio
async def test_breezy_reads_the_public_json_feed_and_the_posting_page():
    host = "acme.breezy.hr"
    feed = f"https://{host}/json"
    posting = f"https://{host}/p/1-engineer"
    http = FakeHttpClient(
        {
            feed: _json_response(
                feed,
                [
                    {
                        "id": "1",
                        "friendly_id": "1-engineer",
                        "name": "Engineer",
                        "url": posting,
                        "published_date": "2026-05-01T10:00:00.000Z",
                        "department": "Engineering",
                    }
                ],
            ),
            posting: _html_response(
                posting,
                '<html><body><div class="description"><p>Build things</p></div></body></html>',
            ),
        }
    )
    connector = BreezyConnector()
    assert connector.matches(f"https://{host}").matched is True
    assert connector.matches("https://app.breezy.hr/acme").matched is True
    assert connector.matches("https://breezy.hr/pricing").matched is False

    source = SourceConfig(id="s1", url=f"https://{host}", kind="ats", connector_id="ats.breezy")
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert [ref.source_posting_id for ref in refs] == ["1-engineer"]
    assert refs[0].updated_at_hint == "2026-05-01T10:00:00.000Z"

    result = await connector.fetch_job(refs[0], source, _ctx(http))
    assert isinstance(result, Ok)
    assert result.value.body_html is not None
    assert "Build things" in result.value.body_html


@pytest.mark.asyncio
async def test_breezy_refuses_a_listing_that_is_no_longer_an_array():
    feed = "https://acme.breezy.hr/json"
    http = FakeHttpClient({feed: _json_response(feed, {"positions": "nope"})})
    source = SourceConfig(
        id="s1", url="https://acme.breezy.hr", kind="ats", connector_id="ats.breezy"
    )

    with pytest.raises(PortError):
        [ref async for ref in await BreezyConnector().list_jobs(source, _ctx(http))]
