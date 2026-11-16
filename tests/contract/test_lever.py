"""Contract: Lever connector parses a fixture board into RawJobRefs."""

from __future__ import annotations

import json

import pytest
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.http import HttpResponse
from app.domain.models.source import SourceConfig
from app.plugins.ats.lever import LeverConnector
from tests.support.fakes import FakeClock, FakeHttpClient, FakeStructuredLogger


def _ctx(http: FakeHttpClient) -> ExecutionContext:
    return ExecutionContext(
        task_id="contract", clock=FakeClock(), http=http, logger=FakeStructuredLogger(),
        cancel=CancellationToken(), deadline=None, config={},
    )


@pytest.mark.asyncio
async def test_lever_matches_and_lists_jobs():
    payload = [{"id": "l1", "text": "Site Reliability Engineer", "hostedUrl": "https://jobs.lever.co/acme/l1", "createdAt": 1700000000}]
    http = FakeHttpClient(
        {
            "https://api.lever.co/v0/postings/acme?mode=json": HttpResponse(
                status=200, url="https://api.lever.co/v0/postings/acme?mode=json",
                headers={"content-type": "application/json"}, body=json.dumps(payload).encode(), elapsed_ms=5,
            )
        }
    )
    connector = LeverConnector()
    assert connector.matches("https://jobs.lever.co/acme").matched is True
    assert connector.matches("https://example.com").matched is False

    source = SourceConfig(id="s1", url="https://jobs.lever.co/acme", kind="ats", connector_id="ats.lever", ats_tenant_key="acme")
    refs = [ref async for ref in await connector.list_jobs(source, _ctx(http))]
    assert len(refs) == 1
    assert refs[0].source_posting_id == "l1"
    assert refs[0].url == "https://jobs.lever.co/acme/l1"
