"""Contract: Ashby connector parses a fixture board into RawJobRefs."""

from __future__ import annotations

import json

import pytest
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.http import HttpResponse
from app.domain.models.source import SourceConfig
from app.plugins.ats.ashby import AshbyConnector
from tests.support.fakes import FakeClock, FakeHttpClient, FakeStructuredLogger


def _ctx(http: FakeHttpClient) -> ExecutionContext:
    return ExecutionContext(
        task_id="contract", clock=FakeClock(), http=http, logger=FakeStructuredLogger(),
        cancel=CancellationToken(), deadline=None, config={},
    )


@pytest.mark.asyncio
async def test_ashby_matches_and_lists_jobs():
    payload = {"jobs": [{"id": "a1", "title": "Platform Engineer", "jobUrl": "https://jobs.ashbyhq.com/acme/a1", "publishedAt": "2026-01-01"}]}
    http = FakeHttpClient(
        {
            "https://api.ashbyhq.com/posting-api/job-board/acme": HttpResponse(
                status=200, url="https://api.ashbyhq.com/posting-api/job-board/acme",
                headers={"content-type": "application/json"}, body=json.dumps(payload).encode(), elapsed_ms=5,
            )
        }
    )
    connector = AshbyConnector()
    assert connector.matches("https://jobs.ashbyhq.com/acme").matched is True
    assert connector.matches("https://example.com").matched is False

    source = SourceConfig(id="s1", url="https://jobs.ashbyhq.com/acme", kind="ats", connector_id="ats.ashby", ats_tenant_key="acme")
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert len(refs) == 1
    assert refs[0].source_posting_id == "a1"
    assert refs[0].url == "https://jobs.ashbyhq.com/acme/a1"
