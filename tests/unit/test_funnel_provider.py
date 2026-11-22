"""M6: the funnel harvests posting URLs from the board-discovery index rows."""

from __future__ import annotations

import json

import pytest
from app.domain.models.common import Ok
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.discovery import DiscoveryQuery
from app.domain.models.http import HttpResponse
from app.plugins.discovery.funnel import FunnelProvider
from app.plugins.discovery.web_index import WebIndexProvider
from tests.support.fakes import FakeClock, FakeHttpClient, FakeStructuredLogger

COLLINFO = json.dumps([{"id": "CC-MAIN-2026-43"}]).encode()


def _rows() -> bytes:
    urls = [
        "https://boards.greenhouse.io/acme",
        "https://boards.greenhouse.io/acme/jobs/123",
        "https://boards.greenhouse.io/acme/jobs/456?utm_source=newsletter",
        "https://boards.greenhouse.io/acme/jobs/123/apply",
        "https://boards.greenhouse.io/acme/images/logo.png",
    ]
    return "".join(
        json.dumps({"url": url, "status": "200", "timestamp": "20260203081435"}) + "\n"
        for url in urls
    ).encode()


class _StubHttp(FakeHttpClient):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    async def send(self, req):
        self.urls.append(req.url)
        body = COLLINFO if "/collinfo" in req.url else _rows()
        return Ok(
            HttpResponse(status=200, url=req.url, headers={}, body=body, elapsed_ms=1)
        )


def _ctx(http: FakeHttpClient, cache: dict[object, object] | None = None):
    return ExecutionContext(
        task_id="test",
        clock=FakeClock(),
        http=http,
        logger=FakeStructuredLogger(),
        cancel=CancellationToken(),
        deadline=None,
        config={"discovery_cache": cache} if cache is not None else {},
    )


@pytest.mark.asyncio
async def test_funnel_harvests_posting_urls_and_ignores_assets() -> None:
    http = _StubHttp()
    provider = FunnelProvider(
        hosts=(("boards.greenhouse.io", "ats.greenhouse"),)
    )
    query = DiscoveryQuery(extra={"cursor": 0, "slices": 1})  # bucket "a" / "acme"

    postings = [p async for p in provider.discover_postings(query, _ctx(http))]

    assert [p.url for p in postings] == [
        "https://boards.greenhouse.io/acme/jobs/123",
        "https://boards.greenhouse.io/acme/jobs/456",
    ]
    assert all(p.board_url == "https://boards.greenhouse.io/acme" for p in postings)
    assert all(p.connector_id == "ats.greenhouse" for p in postings)
    assert [p.source_posting_id for p in postings] == ["123", "456"]
    assert all(p.evidence and p.evidence[0].kind == "web_index" for p in postings)


@pytest.mark.asyncio
async def test_board_discovery_and_funnel_share_one_index_query() -> None:
    http = _StubHttp()
    cache: dict[object, object] = {}
    board_provider = WebIndexProvider(hosts=(("boards.greenhouse.io", "ats.greenhouse"),))
    funnel_provider = FunnelProvider(hosts=(("boards.greenhouse.io", "ats.greenhouse"),))
    query = DiscoveryQuery(extra={"cursor": 0, "slices": 1})
    common = _ctx(http, cache)

    boards = [b async for b in board_provider.discover(query, common)]
    postings = [p async for p in funnel_provider.discover_postings(query, common)]

    assert [b.url for b in boards] == ["https://boards.greenhouse.io/acme"]
    assert len(postings) == 2
    index_queries = [url for url in http.urls if "/collinfo" not in url]
    assert len(index_queries) == 1, "the funnel reuses the cached slice rows"
    assert sum(1 for url in http.urls if "/collinfo" in url) == 1
