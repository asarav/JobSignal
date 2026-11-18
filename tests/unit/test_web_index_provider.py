"""M4: the web-index provider enumerates ATS tenants without a seed list."""

from __future__ import annotations

import json

import pytest
from app.domain.models.common import Err, Ok, PortError
from app.domain.models.context import CancellationToken
from app.domain.models.discovery import DiscoveryQuery
from app.domain.models.http import HttpRequest, HttpResponse
from app.plugins.discovery.web_index import BUCKETS, WebIndexProvider
from tests.support.fakes import FakeClock, FakeHttpClient, FakeStructuredLogger

ENDPOINT = "https://index.example/cdx"


class _StubHttp(FakeHttpClient):
    """Returns one canned index body and records the URLs it was asked for."""

    def __init__(self, body: bytes | None = None, *, fail: bool = False) -> None:
        super().__init__()
        self._body = body
        self._fail = fail
        self.urls: list[str] = []

    async def send(self, req: HttpRequest):
        self.urls.append(req.url)
        if self._fail:
            return Err(PortError(code="http.transport", message="index is down"))
        assert self._body is not None
        return Ok(
            HttpResponse(
                status=200, url=req.url, headers={}, body=self._body, elapsed_ms=1
            )
        )


def _ctx(http) -> object:
    from app.domain.models.context import ExecutionContext

    return ExecutionContext(
        task_id="test",
        clock=FakeClock(),
        http=http,
        logger=FakeStructuredLogger(),
        cancel=CancellationToken(),
        deadline=None,
        config={},
    )


def _body(rows: list[list[str]]) -> bytes:
    return json.dumps([["original"], *rows]).encode()


def _provider(**kwargs) -> WebIndexProvider:
    return WebIndexProvider(endpoint=ENDPOINT, **kwargs)


@pytest.mark.asyncio
async def test_index_rows_become_board_roots():
    http = _StubHttp(
        _body(
            [
                ["https://boards.greenhouse.io/acme"],
                ["https://boards.greenhouse.io/acme/jobs/123"],
                ["https://boards.greenhouse.io/acme/jobs/456?utm_source=x"],
                ["https://boards.greenhouse.io/search"],
                ["https://boards.greenhouse.io/jobs"],
                ["https://boards.greenhouse.io/"],
                ["https://jobs.lever.co/acme"],
                ["https://boards.greenhouse.io/Beta-Corp"],
            ]
        )
    )
    provider = _provider()

    sources = [
        source async for source in provider.discover(DiscoveryQuery(), _ctx(http))
    ]

    assert [s.url for s in sources] == [
        "https://boards.greenhouse.io/acme",
        "https://boards.greenhouse.io/beta-corp",
    ]
    assert sources[0].ats_hint == "ats.greenhouse"
    assert sources[0].company_hint == "Acme"
    assert sources[0].confidence < 1.0
    assert sources[0].evidence and sources[0].evidence[0].kind == "web_index"
    assert http.urls and "boards.greenhouse.io%2Fa" in http.urls[0]
    assert "limit=" in http.urls[0]


@pytest.mark.asyncio
async def test_index_failure_yields_nothing():
    http = _StubHttp(fail=True)
    provider = _provider()

    sources = [
        source async for source in provider.discover(DiscoveryQuery(), _ctx(http))
    ]

    assert sources == []


@pytest.mark.asyncio
async def test_unparseable_index_body_yields_nothing():
    http = _StubHttp(b"<html>rate limited</html>")
    provider = _provider()

    sources = [
        source async for source in provider.discover(DiscoveryQuery(), _ctx(http))
    ]

    assert sources == []


def test_cursor_walks_hosts_then_buckets():
    provider = _provider()
    host_count = 4

    first_host, first_ats, first_bucket = provider.slice_for(0)
    assert first_host == "boards.greenhouse.io"
    assert first_ats == "ats.greenhouse"
    assert first_bucket == BUCKETS[0]

    # The next hosts at the same bucket come before the next bucket.
    assert provider.slice_for(1)[0] == "jobs.ashbyhq.com"
    assert provider.slice_for(host_count)[0] == "boards.greenhouse.io"
    assert provider.slice_for(host_count)[2] == BUCKETS[1]

    # The cursor wraps instead of running off the end.
    assert provider.rotation_size == host_count * len(BUCKETS)
    assert provider.slice_for(provider.rotation_size) == provider.slice_for(0)


@pytest.mark.asyncio
async def test_rotation_cursor_selects_the_queried_slice():
    http = _StubHttp(_body([]))
    provider = _provider()

    # Cursor 1 is the second host at the first bucket.
    _ = [
        s
        async for s in provider.discover(DiscoveryQuery(extra={"cursor": 1}), _ctx(http))
    ]

    assert "jobs.ashbyhq.com%2Fa" in http.urls[0]
