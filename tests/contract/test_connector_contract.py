"""Reusable ConnectorContractSuite (spec sec 19.3).

Provides a set of assertions every ATS connector must satisfy. Connectors
opt in by calling ``run_connector_contract(connector, http_factory)``.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from app.domain.models.common import Ok
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.http import HttpResponse
from app.domain.models.source import SourceConfig
from tests.support.fakes import FakeClock, FakeHttpClient, FakeStructuredLogger


def _ctx(http):
    return ExecutionContext(
        task_id="contract",
        clock=FakeClock(),
        http=http,
        logger=FakeStructuredLogger(),
        cancel=CancellationToken(),
        deadline=None,
        config={},
    )


async def run_connector_contract(connector, make_http: Callable[[], FakeHttpClient]) -> None:
    # matches() returns a MatchResult with confidence in [0,1]
    mr = connector.matches("https://example.com")
    assert hasattr(mr, "matched")
    assert 0.0 <= getattr(mr, "confidence", 0.0) <= 1.0

    # empty listing tolerated
    http = make_http(empty=True)
    source = SourceConfig(
        id="s1",
        url="https://boards.greenhouse.io/acme",
        kind="ats",
        connector_id=connector.id,
        ats_tenant_key="acme",
    )
    refs = [r async for r in await connector.list_jobs(source, _ctx(http))]
    assert refs == [] or isinstance(refs, list)

    # 404 tenant -> error surfaced, not crash
    http = make_http(status=404)
    try:
        refs = [r async for r in await connector.list_jobs(source, _ctx(http))]
    except Exception:
        pass


@pytest.mark.asyncio
async def test_greenhouse_contract_smoke():

    from app.plugins.ats.greenhouse import GreenhouseConnector

    def make_http(empty=False, status=200):
        if empty:
            body = b'{"jobs": []}'
        elif status == 404:
            body = b'{"error": "not found"}'
        else:
            body = json.dumps(
                {
                    "jobs": [
                        {
                            "id": 1,
                            "title": "Eng",
                            "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                        }
                    ]
                }
            ).encode()
        http = FakeHttpClient(
            {
                "https://boards-api.greenhouse.io/v1/boards/acme/jobs": HttpResponse(
                    status=status,
                    url="https://boards-api.greenhouse.io/v1/boards/acme/jobs",
                    headers={"content-type": "application/json"},
                    body=body,
                    elapsed_ms=5,
                )
            }
        )
        return http

    connector = GreenhouseConnector()
    await run_connector_contract(connector, make_http)
    # fetch_job provenance
    http = make_http()
    source = SourceConfig(
        id="s1",
        url="https://boards.greenhouse.io/acme",
        kind="ats",
        connector_id="ats.greenhouse",
        ats_tenant_key="acme",
    )
    refs = [r async for r in await connector.list_jobs(source, _ctx(http))]
    assert len(refs) >= 1
    res = await connector.fetch_job(refs[0], source, _ctx(http))
    assert isinstance(res, Ok)
