"""Contract: Workable, BambooHR and Oracle Recruiting Cloud connectors."""

from __future__ import annotations

import json

import pytest
from app.domain.models.common import Ok
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.http import HttpResponse
from app.domain.models.source import SourceConfig
from app.plugins.ats.bamboohr import BambooHrConnector
from app.plugins.ats.oracle_recruiting import OracleRecruitingConnector
from app.plugins.ats.workable import WorkableConnector
from tests.support.fakes import FakeClock, FakeHttpClient, FakeStructuredLogger


def _ctx(http: FakeHttpClient) -> ExecutionContext:
    return ExecutionContext(
        task_id="contract", clock=FakeClock(), http=http, logger=FakeStructuredLogger(),
        cancel=CancellationToken(), deadline=None, config={},
    )


def _json_response(url: str, payload: dict) -> HttpResponse:
    return HttpResponse(
        status=200,
        url=url,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode(),
        elapsed_ms=5,
    )


@pytest.mark.asyncio
async def test_workable_lists_and_fetches_from_the_widget_feed():
    feed = "https://apply.workable.com/api/v1/widget/accounts/huggingface?details=true"
    http = FakeHttpClient(
        {
            feed: _json_response(
                feed,
                {
                    "name": "Hugging Face",
                    "jobs": [
                        {
                            "title": "ML Engineer",
                            "shortcode": "ABC123",
                            "url": "https://apply.workable.com/j/ABC123",
                            "description": "<p>Build models</p>",
                            "published_on": "2026-05-29",
                            "employment_type": "Full-time",
                            "department": "Engineering",
                            "telecommuting": True,
                            "locations": [
                                {"city": "Paris", "region": "IDF", "country": "France"}
                            ],
                        }
                    ],
                },
            )
        }
    )
    connector = WorkableConnector()
    assert connector.matches("https://apply.workable.com/huggingface").matched is True
    assert connector.matches("https://www.workable.com/pricing").matched is False

    source = SourceConfig(
        id="s1", url="https://apply.workable.com/huggingface", kind="ats",
        connector_id="ats.workable",
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert len(refs) == 1
    assert refs[0].source_posting_id == "ABC123"
    assert refs[0].updated_at_hint == "2026-05-29"

    result = await connector.fetch_job(refs[0], source, _ctx(http))
    assert isinstance(result, Ok)
    assert result.value.title == "ML Engineer"
    assert result.value.body_text == "Build models"
    assert result.value.payload["locations"] == ["Paris, IDF, France", "Remote"]
    assert result.value.payload["companyName"] == "Hugging Face"


@pytest.mark.asyncio
async def test_bamboohr_lists_and_fetches_a_detail_page():
    host = "acme.bamboohr.com"
    list_url = f"https://{host}/careers/list"
    detail_url = f"https://{host}/careers/35/detail"
    http = FakeHttpClient(
        {
            list_url: _json_response(
                list_url,
                {
                    "meta": {"totalCount": 1},
                    "result": [{"id": "35", "jobOpeningName": "Infra Engineer"}],
                },
            ),
            detail_url: _json_response(
                detail_url,
                {
                    "meta": {},
                    "result": {
                        "jobOpening": {
                            "jobOpeningName": "Infra Engineer",
                            "description": "<p>Own infra</p>",
                            "datePosted": "2023-12-22",
                            "employmentType": "Full-time",
                            "location": {"city": "Denver", "state": "CO"},
                            "atsLocation": {"country": "United States"},
                            "jobOpeningShareUrl": f"https://{host}/careers/35",
                        }
                    },
                },
            ),
        }
    )
    connector = BambooHrConnector()
    assert connector.matches("https://acme.bamboohr.com/careers").matched is True
    assert connector.matches("https://www.bamboohr.com").matched is False

    source = SourceConfig(
        id="s1", url=f"https://{host}/careers", kind="ats", connector_id="ats.bamboohr",
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert len(refs) == 1
    assert refs[0].url == f"https://{host}/careers/35"

    result = await connector.fetch_job(refs[0], source, _ctx(http))
    assert isinstance(result, Ok)
    assert result.value.title == "Infra Engineer"
    assert result.value.body_text == "Own infra"
    assert result.value.payload["posted_at"] == "2023-12-22"
    assert result.value.payload["locations"] == ["Denver, CO, United States"]


ORACLE_HOST = "acme.fa.us2.oraclecloud.com"
ORACLE_SOURCE = f"https://{ORACLE_HOST}/hcmUI/CandidateExperience/en/sites/CX_1/requisitions"
ORACLE_LIST = (
    f"https://{ORACLE_HOST}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    "?onlyData=true&expand=requisitionList.secondaryLocations"
    "&finder=findReqs;siteNumber=CX_1,limit=100,offset=0,sortBy=POSTING_DATES_DESC"
)
ORACLE_DETAIL = (
    f"https://{ORACLE_HOST}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
    '?finder=ById;Id="7",siteNumber=CX_1&onlyData=true'
)


@pytest.mark.asyncio
async def test_oracle_lists_and_fetches_a_requisition():
    http = FakeHttpClient(
        {
            ORACLE_LIST: _json_response(
                ORACLE_LIST,
                {
                    "items": [
                        {
                            "TotalJobsCount": 1,
                            "requisitionList": [
                                {
                                    "Id": "7",
                                    "Title": "Data Engineer",
                                    "PostedDate": "2026-11-01",
                                    "PrimaryLocation": "Berlin, Germany",
                                }
                            ],
                        }
                    ]
                },
            ),
            ORACLE_DETAIL: _json_response(
                ORACLE_DETAIL,
                {
                    "items": [
                        {
                            "Id": "7",
                            "Title": "Data Engineer",
                            "ExternalDescriptionStr": "<p>Build pipelines</p>",
                            "ExternalResponsibilitiesStr": "<p>Own them</p>",
                            "ExternalQualificationsStr": None,
                            "PrimaryLocation": "Berlin, Germany",
                            "ExternalPostedStartDate": "2026-11-01",
                            "WorkplaceType": "Hybrid",
                        }
                    ]
                },
            ),
        }
    )
    connector = OracleRecruitingConnector()
    assert connector.matches(ORACLE_SOURCE).matched is True
    assert connector.matches("https://example.com/jobs").matched is False

    source = SourceConfig(
        id="s1", url=ORACLE_SOURCE, kind="ats", connector_id="ats.oracle_recruiting",
    )
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert len(refs) == 1
    assert refs[0].source_posting_id == "7"
    assert refs[0].url == (
        f"https://{ORACLE_HOST}/hcmUI/CandidateExperience/en/sites/CX_1/job/7"
    )

    result = await connector.fetch_job(refs[0], source, _ctx(http))
    assert isinstance(result, Ok)
    assert result.value.title == "Data Engineer"
    assert "Build pipelines" in (result.value.body_text or "")
    assert "Own them" in (result.value.body_text or "")
    assert result.value.payload["workplaceType"] == "Hybrid"
    assert result.value.payload["locations"] == ["Berlin, Germany"]


def test_classifier_maps_the_new_hosts():
    from app.domain.services.classifier import classify_url

    assert classify_url("https://apply.workable.com/acme")[0] == "ats.workable"
    assert classify_url("https://acme.bamboohr.com/careers")[0] == "ats.bamboohr"
    assert classify_url(ORACLE_SOURCE)[0] == "ats.oracle_recruiting"
