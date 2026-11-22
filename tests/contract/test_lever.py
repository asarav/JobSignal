"""Contract: Lever connector parses a fixture board into RawJobRefs."""

from __future__ import annotations

import json

import pytest
from app.domain.models.common import Ok, PortError
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.http import HttpResponse
from app.domain.models.job import RawJobRef
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


@pytest.mark.asyncio
async def test_lever_fetch_job_reads_the_posting_detail():
    payload = {
        "id": "l1",
        "text": "Site Reliability Engineer",
        "descriptionPlain": "Keep the site up.",
        "description": "<p>Keep the site up.</p>",
        "lists": [{"text": "Requirements", "content": "<li>Kubernetes</li>"}],
        "categories": {"location": "Remote", "team": "Platform"},
    }
    http = FakeHttpClient(
        {
            "https://api.lever.co/v0/postings/acme/l1": HttpResponse(
                status=200,
                url="https://api.lever.co/v0/postings/acme/l1",
                headers={"content-type": "application/json"},
                body=json.dumps(payload).encode(),
                elapsed_ms=5,
            )
        }
    )
    connector = LeverConnector()
    source = SourceConfig(id="s1", url="https://jobs.lever.co/acme", kind="ats", connector_id="ats.lever", ats_tenant_key="acme")
    ref = RawJobRef(source_id="s1", url="https://jobs.lever.co/acme/l1", source_posting_id="l1")

    result = await connector.fetch_job(ref, source, _ctx(http))

    assert isinstance(result, Ok)
    assert result.value.title == "Site Reliability Engineer"
    assert "Keep the site up." in (result.value.body_text or "")
    assert "Kubernetes" in (result.value.body_text or "")
    assert "Requirements" in (result.value.body_html or "")
    assert result.value.payload["location"] == "Remote"


@pytest.mark.asyncio
async def test_lever_fetch_job_fails_loudly_for_a_withdrawn_posting():
    http = FakeHttpClient(
        {
            "https://api.lever.co/v0/postings/acme/gone": HttpResponse(
                status=200,
                url="https://api.lever.co/v0/postings/acme/gone",
                headers={"content-type": "application/json"},
                body=json.dumps({"ok": False, "error": "posting not found"}).encode(),
                elapsed_ms=5,
            )
        }
    )
    connector = LeverConnector()
    source = SourceConfig(id="s1", url="https://jobs.lever.co/acme", kind="ats", connector_id="ats.lever", ats_tenant_key="acme")
    ref = RawJobRef(source_id="s1", url="https://jobs.lever.co/acme/gone", source_posting_id="gone")

    with pytest.raises(PortError) as raised:
        await connector.fetch_job(ref, source, _ctx(http))

    assert raised.value.code == "connector.not_found"
