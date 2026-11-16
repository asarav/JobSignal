"""Contract: Greenhouse connector parses a fixture board into RawJobRefs."""

from __future__ import annotations

import json

import pytest
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.http import HttpResponse
from app.domain.models.source import SourceConfig
from app.plugins.ats.greenhouse import GreenhouseConnector
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


@pytest.mark.asyncio
async def test_greenhouse_matches_and_lists_jobs():
    payload = {
        "jobs": [
            {"id": 1, "title": "Backend Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "updated_at": "2026-01-01T00:00:00Z"},
            {"id": 2, "title": "Frontend Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/2", "updated_at": "2026-01-02T00:00:00Z"},
        ]
    }
    http = FakeHttpClient(
        {
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs": HttpResponse(
                status=200,
                url="https://boards-api.greenhouse.io/v1/boards/acme/jobs",
                headers={"content-type": "application/json"},
                body=json.dumps(payload).encode(),
                elapsed_ms=5,
            )
        }
    )
    connector = GreenhouseConnector()
    assert connector.matches("https://boards.greenhouse.io/acme").matched is True
    assert connector.matches("https://example.com").matched is False

    source = SourceConfig(id="s1", url="https://boards.greenhouse.io/acme", kind="ats", connector_id="ats.greenhouse", ats_tenant_key="acme")
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert len(refs) == 2
    assert refs[0].source_posting_id == "1"
    assert refs[0].url == "https://boards.greenhouse.io/acme/jobs/1"


@pytest.mark.asyncio
async def test_greenhouse_fetch_job_returns_raw():
    http = FakeHttpClient(
        {
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs/1": HttpResponse(
                status=200,
                url="https://boards-api.greenhouse.io/v1/boards/acme/jobs/1",
                headers={"content-type": "application/json"},
                body=json.dumps({"id": 1, "title": "Backend Engineer", "content": "build things"}).encode(),
                elapsed_ms=5,
            )
        }
    )
    connector = GreenhouseConnector()
    from app.domain.models.job import RawJobRef

    ref = RawJobRef(source_id="s1", url="https://boards.greenhouse.io/acme/jobs/1", source_posting_id="1")
    result = await connector.fetch_job(ref, _ctx(http))
    from app.domain.models.common import Ok

    assert isinstance(result, Ok)
    assert result.value.title == "Backend Engineer"
    assert result.value.body_text == "build things"
