"""Contract: the SmartRecruiters, Workday and iCIMS connectors (M2)."""

from __future__ import annotations

import json

import pytest
from app.domain.models.common import Ok
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.http import HttpRequest, HttpResponse
from app.domain.models.source import SourceConfig
from app.plugins.ats.icims import IcmsConnector
from app.plugins.ats.smartrecruiters import SmartRecruitersConnector
from app.plugins.ats.workday import WorkdayConnector
from tests.support.fakes import FakeClock, FakeHttpClient, FakeStructuredLogger


def _ctx(http: FakeHttpClient) -> ExecutionContext:
    return ExecutionContext(
        task_id="contract", clock=FakeClock(), http=http, logger=FakeStructuredLogger(),
        cancel=CancellationToken(), deadline=None, config={},
    )


def _response(url: str, payload: dict) -> HttpResponse:
    return HttpResponse(
        status=200,
        url=url,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode(),
        elapsed_ms=5,
    )


@pytest.mark.asyncio
async def test_smartrecruiters_lists_and_fetches_postings():
    list_url = "https://api.smartrecruiters.com/v1/companies/acme/postings?limit=100"
    detail_url = "https://api.smartrecruiters.com/v1/companies/acme/postings/101"
    http = FakeHttpClient(
        {
            list_url: _response(
                list_url,
                {
                    "content": [
                        {
                            "id": 101,
                            "name": "Platform Engineer",
                            "ref": "https://jobs.smartrecruiters.com/acme/101",
                            "releasedDate": "2026-01-01",
                        }
                    ],
                    "total": 1,
                },
            ),
            detail_url: _response(
                detail_url,
                {
                    "id": 101,
                    "name": "Platform Engineer",
                    "jobDescription": "<p>Build platforms</p>",
                    "typeOfEmployment": "FULLTIME",
                },
            ),
        }
    )
    connector = SmartRecruitersConnector()
    assert connector.matches("https://jobs.smartrecruiters.com/acme").matched is True

    source = SourceConfig(
        id="s1", url="https://jobs.smartrecruiters.com/acme", kind="ats",
        connector_id="ats.smartrecruiters", ats_tenant_key="acme",
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert len(refs) == 1
    assert refs[0].source_posting_id == "101"

    result = await connector.fetch_job(refs[0], source, _ctx(http))
    assert isinstance(result, Ok)
    assert result.value.title == "Platform Engineer"
    assert result.value.body_text == "<p>Build platforms</p>"
    assert result.value.payload["typeOfEmployment"] == "FULLTIME"


@pytest.mark.asyncio
async def test_workday_lists_and_fetches_postings():
    host = "wd3.myworkdayjobs.com"
    list_url = f"https://{host}/wday/cxs/acme/acme/jobs"
    detail_url = f"https://{host}/wday/cxs/acme/acme/en-US/acme/Data-Engineer_R-123"
    http = FakeHttpClient(
        {
            list_url: _response(
                list_url,
                {
                    "jobPostings": [
                        {
                            "title": "Data Engineer",
                            "externalPath": "/en-US/acme/Data-Engineer_R-123",
                            "locationsText": "Berlin",
                        }
                    ],
                    "total": 1,
                },
            ),
            detail_url: _response(
                detail_url,
                {
                    "jobPostingInfo": {
                        "title": "Data Engineer",
                        "jobDescription": "<p>Own the models</p>",
                    }
                },
            ),
        }
    )
    connector = WorkdayConnector()
    assert connector.matches(f"https://{host}/en-US/acme").matched is True

    source = SourceConfig(
        id="s1", url=f"https://{host}/en-US/acme", kind="ats",
        connector_id="ats.workday", ats_tenant_key="acme",
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert len(refs) == 1
    assert refs[0].url.endswith("/en-US/acme/Data-Engineer_R-123")

    result = await connector.fetch_job(refs[0], source, _ctx(http))
    assert isinstance(result, Ok)
    assert result.value.title == "Data Engineer"
    assert result.value.body_text == "<p>Own the models</p>"


@pytest.mark.asyncio
async def test_icims_lists_postings_from_the_search_page():
    search_url = "https://careers.acme.icims.com/jobs/search?search=&limit=100"
    html = """
    <html><body>
      <ul>
        <li><a href="/job/12345/Software-Engineer">Software Engineer</a></li>
        <li><a href="/job/12346/Data-Engineer">Data Engineer</a></li>
        <li><a href="/about">About us</a></li>
      </ul>
    </body></html>
    """
    http = FakeHttpClient(
        {
            search_url: HttpResponse(
                status=200, url=search_url, headers={"content-type": "text/html"},
                body=html.encode(), elapsed_ms=5,
            )
        }
    )
    connector = IcmsConnector()
    assert connector.matches("https://careers.acme.icims.com/jobs").matched is True

    source = SourceConfig(
        id="s1", url="https://careers.acme.icims.com/jobs", kind="ats",
        connector_id="ats.icims",
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert len(refs) == 2
    assert refs[0].url == "https://careers.acme.icims.com/job/12345/Software-Engineer"


@pytest.mark.asyncio
async def test_icims_returns_the_job_page_html_for_the_pipeline():
    job_url = "https://careers.acme.icims.com/job/12345/Software-Engineer"
    html = """
    <html><head><script type="application/ld+json">
    {"@type":"JobPosting","title":"Software Engineer",
     "description":"Build the product."}
    </script></head><body></body></html>
    """
    http = FakeHttpClient(
        {
            job_url: HttpResponse(
                status=200, url=job_url, headers={"content-type": "text/html"},
                body=html.encode(), elapsed_ms=5,
            )
        }
    )
    connector = IcmsConnector()
    from app.domain.models.job import RawJobRef
    from app.domain.models.source import SourceConfig

    source = SourceConfig(id="s1", url="https://careers.acme.com/jobs", kind="ats", connector_id="ats.icims")
    result = await connector.fetch_job(
        RawJobRef(source_id="s1", url=job_url), source, _ctx(http)
    )
    assert isinstance(result, Ok)
    assert "Software Engineer" in (result.value.body_html or "")


@pytest.mark.asyncio
async def test_connector_posts_the_workday_search_body():
    """The Workday listing uses a POST body; the connector must send it."""
    list_url = "https://wd3.myworkdayjobs.com/wday/cxs/acme/acme/jobs"
    http = FakeHttpClient({list_url: _response(list_url, {"jobPostings": []})})
    connector = WorkdayConnector()
    source = SourceConfig(
        id="s1", url="https://wd3.myworkdayjobs.com/en-US/acme", kind="ats",
        connector_id="ats.workday", ats_tenant_key="acme",
    )

    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert refs == []
    request = http.sent[0]
    assert isinstance(request, HttpRequest)
    assert request.method == "POST"
    assert request.json_body is not None
    assert request.json_body["limit"] == 20
