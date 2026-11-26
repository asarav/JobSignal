"""Contract: Eightfold, Phenom, Amazon, Taleo and wrapper routing."""

from __future__ import annotations

import json

import pytest
from app.domain.models.common import Ok
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.http import HttpResponse
from app.domain.models.source import SourceConfig
from app.domain.services.wrappers import detect_embedded_ats
from app.plugins.ats.amazon import AmazonConnector
from app.plugins.ats.career_site import CareerSiteConnector
from app.plugins.ats.eightfold import EightfoldConnector
from app.plugins.ats.phenom import PhenomConnector
from app.plugins.ats.taleo import TaleoConnector
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


def _json_response(url: str, payload: dict) -> HttpResponse:
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


EIGHTFOLD_SOURCE = "https://acme.eightfold.ai/careers"
EIGHTFOLD_LIST = "https://acme.eightfold.ai/api/apply/v2/jobs?domain=acme.com&start=0&num=100"
EIGHTFOLD_DETAIL = "https://acme.eightfold.ai/api/apply/v2/jobs/42?domain=acme.com"


@pytest.mark.asyncio
async def test_eightfold_lists_and_fetches_a_position():
    http = FakeHttpClient(
        {
            EIGHTFOLD_LIST: _json_response(
                EIGHTFOLD_LIST,
                {
                    "count": 1,
                    "positions": [
                        {
                            "id": 42,
                            "name": "Platform Engineer",
                            "canonicalPositionUrl": "https://acme.eightfold.ai/careers/job/42",
                            "location": "Berlin, Germany",
                            "t_create": 1721692800,
                            "type": "Full-time",
                            "department": "Engineering",
                        }
                    ],
                },
            ),
            EIGHTFOLD_DETAIL: _json_response(
                EIGHTFOLD_DETAIL,
                {
                    "id": 42,
                    "name": "Platform Engineer",
                    "job_description": "<p>Own the platform.</p>",
                    "location": "Berlin, Germany",
                    "t_create": 1721692800,
                    "canonicalPositionUrl": "https://acme.eightfold.ai/careers/job/42",
                },
            ),
        }
    )
    connector = EightfoldConnector()
    assert connector.matches(EIGHTFOLD_SOURCE).matched is True
    assert connector.matches("https://example.com/jobs").matched is False

    source = SourceConfig(
        id="s1",
        url=EIGHTFOLD_SOURCE,
        kind="ats",
        connector_id="ats.eightfold",
        ats_tenant_key="acme.com",
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert len(refs) == 1
    assert refs[0].source_posting_id == "42"
    assert refs[0].url.endswith("/careers/job/42")

    result = await connector.fetch_job(refs[0], source, _ctx(http))
    assert isinstance(result, Ok)
    assert result.value.title == "Platform Engineer"
    assert result.value.body_text == "Own the platform."
    assert result.value.payload["locations"] == ["Berlin, Germany"]


PHENOM_SOURCE = "https://careers.acme.com/us/en/home"
PHENOM_ENDPOINT = "https://careers.acme.com/widgets"


def _phenom_search() -> dict:
    return {
        "refineSearch": {
            "status": 200,
            "hits": 1,
            "totalHits": 1,
            "data": {
                "jobs": [
                    {
                        "jobId": "R-1",
                        "title": "Data Analyst",
                        "location": "Dallas, Texas",
                        "applyUrl": "https://acme.wd1.myworkdayjobs.com/external/job/1",
                        "dateCreated": "2026-11-04T21:50:32.011+0000",
                        "category": "Corporate",
                        "type": "Full time",
                    }
                ]
            },
        }
    }


def _phenom_detail() -> dict:
    return {
        "jobDetail": {
            "status": 200,
            "hits": 1,
            "data": {
                "job": {
                    "jobId": "R-1",
                    "title": "Data Analyst",
                    "location": "Dallas, Texas",
                    "description": "<p>Analyse the network.</p>",
                    "qualifications": "<ul><li>SQL</li></ul>",
                    "applyUrl": "https://acme.wd1.myworkdayjobs.com/external/job/1",
                }
            },
        }
    }


@pytest.mark.asyncio
async def test_phenom_uses_the_widget_endpoint_and_field_mapping():
    http = FakeHttpClient(
        {
            PHENOM_ENDPOINT: _json_response(PHENOM_ENDPOINT, {}),
        }
    )
    # Both list and detail POST to the same URL; branch on the request body.
    calls: list[dict] = []

    class _PhenomHttp(FakeHttpClient):
        async def send(self, req):
            calls.append(req.json_body or {})
            payload = (
                _phenom_detail()
                if (req.json_body or {}).get("ddoKey") == "jobDetail"
                else _phenom_search()
            )
            return Ok(_json_response(req.url, payload))

    http = _PhenomHttp()
    connector = PhenomConnector()
    assert (
        connector.matches(
            "https://careers.acme.com", sniff=_sniff("<script>ddoKey</script>")
        ).matched
        is True
    )
    source = SourceConfig(
        id="s1",
        url=PHENOM_SOURCE,
        kind="ats",
        connector_id="ats.phenom",
        limits={
            "phenom": {
                "endpoint_template": "https://{host}/widgets",
                "field_map": {"id": "jobId", "title": "title", "description": "description"},
            }
        },
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert len(refs) == 1
    assert refs[0].source_posting_id == "R-1"
    assert calls[0]["ddoKey"] == "refineSearch"

    result = await connector.fetch_job(refs[0], source, _ctx(http))
    assert isinstance(result, Ok)
    assert result.value.title == "Data Analyst"
    assert "Analyse the network" in (result.value.body_text or "")
    assert result.value.payload["locations"] == ["Dallas, Texas"]


def _sniff(html: str):
    from app.domain.models.source import PageSniff

    return PageSniff(url="https://careers.acme.com", html_excerpt=html)


AMAZON_LIST = (
    "https://www.amazon.jobs/en/search.json?base_query=&loc_query=&result_limit=100&offset=0"
)
AMAZON_DETAIL = "https://www.amazon.jobs/en/jobs/10431053/role"


@pytest.mark.asyncio
async def test_amazon_lists_from_search_json_and_fetches_detail_html():
    http = FakeHttpClient(
        {
            AMAZON_LIST: _json_response(
                AMAZON_LIST,
                {
                    "hits": 1,
                    "jobs": [
                        {
                            "id": "uuid",
                            "id_icims": "10431053",
                            "title": "Procurement Manager",
                            "job_path": "/en/jobs/10431053/role",
                            "posted_date": "May 27, 2026",
                        }
                    ],
                },
            ),
            AMAZON_DETAIL: _html_response(
                AMAZON_DETAIL, "<html><body><h1>Procurement Manager</h1></body></html>"
            ),
        }
    )
    connector = AmazonConnector()
    assert connector.matches("https://www.amazon.jobs/en/search").matched is True
    source = SourceConfig(
        id="s1",
        url="https://www.amazon.jobs/en/search",
        kind="ats",
        connector_id="ats.amazon",
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert len(refs) == 1
    assert refs[0].source_posting_id == "10431053"

    result = await connector.fetch_job(refs[0], source, _ctx(http))
    assert isinstance(result, Ok)
    assert result.value.title == "Procurement Manager"


TALEO_SEARCH = "https://acme.taleo.net/careersection/2/jobsearch.ftl?lang=en"
TALEO_DETAIL = "https://acme.taleo.net/careersection/2/jobdetail.ftl?job=99&lang=en"


@pytest.mark.asyncio
async def test_taleo_lists_jobdetail_links_and_returns_the_detail():
    http = FakeHttpClient(
        {
            TALEO_SEARCH: _html_response(
                TALEO_SEARCH,
                f'<a href="{TALEO_DETAIL}">Data Engineer</a>'
                '<a href="/careersection/2/jobsearch.ftl?lang=en">Search</a>',
            ),
            TALEO_DETAIL: _html_response(
                TALEO_DETAIL, "<html><body><h1>Data Engineer</h1></body></html>"
            ),
        }
    )
    connector = TaleoConnector()
    assert connector.matches(TALEO_SEARCH).matched is True
    source = SourceConfig(
        id="s1",
        url=TALEO_SEARCH,
        kind="ats",
        connector_id="ats.taleo",
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert [ref.url for ref in refs] == [TALEO_DETAIL]

    result = await connector.fetch_job(refs[0], source, _ctx(http))
    assert isinstance(result, Ok)
    assert "Data Engineer" in (result.value.body_html or "")


def test_wrapper_detection_routes_embedded_workday_oracle_and_phenom():
    workday = detect_embedded_ats(
        '<iframe src="https://swa.wd1.myworkdayjobs.com/external/job/TX/1"></iframe>',
        "https://careers.example.com",
    )
    assert workday is not None
    assert workday.connector_id == "ats.workday"
    assert workday.url == "https://swa.wd1.myworkdayjobs.com/external"
    assert workday.tenant_key == "swa"

    oracle = detect_embedded_ats(
        '<script>var url="https:\\/\\/acme.fa.us2.oraclecloud.com\\/hcmUI\\/'
        'CandidateExperience\\/en\\/sites\\/CX_1\\/requisitions";</script>',
        "https://careers.example.com",
    )
    assert oracle is not None
    assert oracle.connector_id == "ats.oracle_recruiting"
    assert "CX_1" in oracle.url

    phenom = detect_embedded_ats(
        "<script>window.ddoKey = 'refineSearch'</script>", "https://careers.acme.com"
    )
    assert phenom is not None
    assert phenom.connector_id == "ats.phenom"
    assert phenom.url == "https://careers.acme.com"

    eightfold = detect_embedded_ats(
        '<script>window._EF_GROUP_ID = "netflix.com";</script>',
        "https://explore.jobs.netflix.net/careers",
    )
    assert eightfold is not None
    assert eightfold.connector_id == "ats.eightfold"
    assert eightfold.url == "https://explore.jobs.netflix.net/careers"

    assert detect_embedded_ats("<html>no ats here</html>", "https://example.com") is None


@pytest.mark.asyncio
async def test_career_site_routes_an_embedded_workday_wrapper():
    page = "https://careers.example.com"
    workday_list = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Careers/jobs"
    detail_path = "/Careers/job/Berlin/Engineer_R1"
    http = FakeHttpClient(
        {
            page: _html_response(
                page,
                '<iframe src="https://acme.wd5.myworkdayjobs.com/Careers"></iframe>',
            ),
            workday_list: _json_response(
                workday_list,
                {
                    "jobPostings": [
                        {
                            "title": "Engineer",
                            "externalPath": detail_path,
                            "titleReqId": "R1",
                        }
                    ]
                },
            ),
        }
    )
    connector = CareerSiteConnector()
    source = SourceConfig(
        id="s1",
        url=page,
        kind="career_site",
        connector_id="ats.career_site",
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert len(refs) == 1
    assert refs[0].source_id == "s1"
    assert refs[0].url == f"https://acme.wd5.myworkdayjobs.com{detail_path}"
    assert any(req.method == "POST" for req in http.sent)


@pytest.mark.asyncio
async def test_career_site_routes_a_phenom_custom_host():
    page = "https://careers.acme.com"

    class _PhenomHttp(FakeHttpClient):
        async def send(self, req):
            if req.url == page:
                return Ok(_html_response(page, "<script>window.ddoKey='refineSearch'</script>"))
            return Ok(_json_response(req.url, _phenom_search()))

    connector = CareerSiteConnector()
    source = SourceConfig(
        id="s1",
        url=page,
        kind="career_site",
        connector_id="ats.career_site",
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(_PhenomHttp()))]
    assert len(refs) == 1
    assert refs[0].source_posting_id == "R-1"


@pytest.mark.asyncio
async def test_career_site_harvests_job_links_when_no_ats_is_embedded():
    page = "https://careers.example.com"
    http = FakeHttpClient(
        {
            page: _html_response(
                page,
                '<a href="/jobs/1">Engineer</a>'
                '<a href="/jobs/search">Search</a>'
                '<a href="/privacy">Privacy</a>',
            )
        }
    )
    connector = CareerSiteConnector()
    source = SourceConfig(
        id="s1",
        url=page,
        kind="career_site",
        connector_id="ats.career_site",
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert [ref.url for ref in refs] == [f"{page}/jobs/1"]


@pytest.mark.asyncio
async def test_career_site_reads_a_sitemap_for_job_urls():
    page = "https://careers.example.com"
    sitemap = "https://careers.example.com/sitemap.xml"
    detail = "https://careers.example.com/en-us/details/42/engineer"
    http = FakeHttpClient(
        {
            page: _html_response(page, "<html><body>Careers</body></html>"),
            sitemap: HttpResponse(
                status=200,
                url=sitemap,
                headers={"content-type": "application/xml"},
                body=(
                    "<urlset><url><loc>" + detail + "</loc></url>"
                    "<url><loc>https://careers.example.com/privacy</loc></url></urlset>"
                ).encode(),
                elapsed_ms=1,
            ),
        }
    )
    connector = CareerSiteConnector()
    source = SourceConfig(
        id="s1",
        url=page,
        kind="career_site",
        connector_id="ats.career_site",
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert [ref.url for ref in refs] == [detail]


@pytest.mark.asyncio
async def test_google_search_page_harvests_only_job_cards():
    page = "https://www.google.com/about/careers/applications/jobs/results/"
    detail = "https://www.google.com/about/careers/applications/jobs/results/123-staff-engineer"
    http = FakeHttpClient(
        {
            page: _html_response(
                page,
                '<a href="jobs/results/123-staff-engineer">Engineer</a>'
                '<a href="jobs/results/how-we-hire">How we hire</a>',
            )
        }
    )
    connector = CareerSiteConnector()
    source = SourceConfig(
        id="s1",
        url=page,
        kind="career_site",
        connector_id="ats.career_site",
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert [ref.url for ref in refs] == [detail]


@pytest.mark.asyncio
async def test_apple_search_page_harvests_public_detail_links():
    page = "https://jobs.apple.com/en-us/search?query=engineer"
    detail = "https://jobs.apple.com/en-us/details/114438346/specialist"
    http = FakeHttpClient({page: _html_response(page, f'<a href="{detail}">Specialist</a>')})
    connector = CareerSiteConnector()
    source = SourceConfig(
        id="s1",
        url=page,
        kind="career_site",
        connector_id="ats.career_site",
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert [ref.url for ref in refs] == [detail]


@pytest.mark.asyncio
async def test_taleo_lists_postings_through_its_faceted_search_endpoint():
    # Current sections render the list client-side; the page names the portal
    # and the endpoint answers the same JSON the page's own script renders.
    search = "https://acme.taleo.net/careersection/2/jobsearch.ftl?lang=en"
    endpoint = (
        "https://acme.taleo.net/careersection/rest/jobboard/searchjobs?lang=en&portal=101430233"
    )
    http = FakeHttpClient(
        {
            search: _html_response(
                search, '<script>queryString: "lang=en&portal=101430233"</script>'
            ),
            endpoint: _json_response(
                endpoint,
                {
                    "pagingData": {"currentPageNo": 1, "pageSize": 25, "totalCount": 2},
                    "requisitionList": [{"jobId": "99"}, {"jobId": "100"}],
                },
            ),
        }
    )
    connector = TaleoConnector()
    source = SourceConfig(
        id="s1",
        url=search,
        kind="ats",
        connector_id="ats.taleo",
    )

    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]

    assert [ref.url for ref in refs] == [
        "https://acme.taleo.net/careersection/2/jobdetail.ftl?job=99&lang=en",
        "https://acme.taleo.net/careersection/2/jobdetail.ftl?job=100&lang=en",
    ]
    posted = [req for req in http.sent if req.method == "POST"]
    assert posted and posted[0].headers["tz"] == "UTC"
