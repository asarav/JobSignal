"""Stopgap coverage for upstream ATS schema drift."""
from __future__ import annotations

import json

import pytest
from app.domain.models.common import Ok, PortError
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.http import HttpResponse
from app.domain.models.source import SourceConfig
from app.plugins.ats.amazon import AmazonConnector
from app.plugins.ats.schema import listing_items
from app.plugins.ats.workday import WorkdayConnector
from tests.support.fakes import FakeClock, FakeHttpClient, FakeStructuredLogger

HOST = "wd3.myworkdayjobs.com"
LIST_URL = f"https://{HOST}/wday/cxs/acme/acme/jobs"
PUBLIC_URL = f"https://{HOST}/en-US/acme/Data-Engineer_R-123"
DETAIL_URL = f"https://{HOST}/wday/cxs/acme/acme/en-US/acme/Data-Engineer_R-123"


def _ctx(http: FakeHttpClient) -> ExecutionContext:
    return ExecutionContext(
        task_id="drift",
        clock=FakeClock(),
        http=http,
        logger=FakeStructuredLogger(),
        cancel=CancellationToken(),
        deadline=None,
        config={},
    )


def _response(url: str, payload: dict) -> HttpResponse:
    return HttpResponse(
        status=200,
        url=url,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode(),
        elapsed_ms=5,
    )


def _source() -> SourceConfig:
    return SourceConfig(
        id="s1",
        url=f"https://{HOST}/en-US/acme",
        kind="ats",
        connector_id="ats.workday",
        ats_tenant_key="acme",
    )


def test_listing_items_treats_only_explicit_empties_as_empty() -> None:
    assert listing_items({"jobPostings": [{"title": "x"}]}, "jobPostings", ats="workday")
    assert listing_items({"jobPostings": []}, "jobPostings", ats="workday") == []
    assert listing_items({"total": 0}, "jobPostings", ats="workday") == []
    # Later pages legitimately end without the list.
    assert listing_items({}, "jobPostings", ats="workday", first_page=False) == []


def test_listing_items_flags_a_renamed_key() -> None:
    with pytest.raises(PortError) as excinfo:
        listing_items({"total": 12, "postings": []}, "jobPostings", ats="workday")
    assert excinfo.value.code == "connector.schema_drift"
    assert "jobPostings" in excinfo.value.message


async def test_workday_listing_reports_schema_drift() -> None:
    http = FakeHttpClient(
        {LIST_URL: _response(LIST_URL, {"total": 3, "postings": [{"title": "x"}]})}
    )
    with pytest.raises(PortError) as excinfo:
        [ref async for ref in await WorkdayConnector().list_jobs(_source(), _ctx(http))]
    assert excinfo.value.code == "connector.schema_drift"


async def test_workday_detail_falls_back_to_the_public_page() -> None:
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type":"JobPosting","title":"Data Engineer"}'
        "</script></head></html>"
    )
    http = FakeHttpClient(
        {
            LIST_URL: _response(
                LIST_URL,
                {
                    "total": 1,
                    "jobPostings": [
                        {
                            "title": "Data Engineer",
                            "externalPath": "/en-US/acme/Data-Engineer_R-123",
                        }
                    ],
                },
            ),
            DETAIL_URL: _response(DETAIL_URL, {"unexpected": {"shape": True}}),
            PUBLIC_URL: HttpResponse(
                status=200,
                url=PUBLIC_URL,
                headers={"content-type": "text/html"},
                body=html.encode(),
                elapsed_ms=7,
            ),
        }
    )
    connector = WorkdayConnector()
    refs = [ref async for ref in await connector.list_jobs(_source(), _ctx(http))]
    result = await connector.fetch_job(refs[0], _source(), _ctx(http))

    assert isinstance(result, Ok)
    # No invented fields: the extraction pipeline gets the untouched HTML.
    assert result.value.title is None
    assert "JobPosting" in (result.value.body_html or "")


async def test_amazon_listing_reports_schema_drift() -> None:
    # The fake answers an unmapped URL with a non-JSON body.
    http = FakeHttpClient()
    source = SourceConfig(
        id="s1",
        url="https://www.amazon.jobs/en/",
        kind="ats",
        connector_id="ats.amazon",
    )
    with pytest.raises(PortError) as excinfo:
        [ref async for ref in await AmazonConnector().list_jobs(source, _ctx(http))]
    assert excinfo.value.code == "connector.schema_drift"
