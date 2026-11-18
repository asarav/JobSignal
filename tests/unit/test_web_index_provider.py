"""M4: the web-index provider enumerates ATS tenants without a seed list."""

from __future__ import annotations

import json

import pytest
from app.domain.models.common import Err, Ok, PortError
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.discovery import DiscoveryQuery
from app.domain.models.http import HttpRequest, HttpResponse
from app.plugins.discovery.web_index import BUCKETS, WebIndexProvider
from tests.support.fakes import FakeClock, FakeHttpClient, FakeStructuredLogger

COLL_ID = "CC-MAIN-2026-43"
OLDER_ID = "CC-MAIN-2026-30"


class _StubHttp(FakeHttpClient):
    """Serves a canned crawl-index listing and index bodies by URL."""

    def __init__(
        self,
        index_body: bytes | None = None,
        *,
        fail: bool = False,
        collinfo_body: bytes | None = None,
        bodies_by_url: dict[str, bytes] | None = None,
    ) -> None:
        super().__init__()
        self._fail = fail
        self._index_body = index_body
        self._collinfo_body = collinfo_body
        self._bodies_by_url = bodies_by_url or {}
        self.urls: list[str] = []

    async def send(self, req: HttpRequest):
        self.urls.append(req.url)
        if self._fail:
            return Err(PortError(code="http.transport", message="index is down"))
        if "/collinfo" in req.url:
            body = self._collinfo_body or json.dumps(
                [{"id": COLL_ID}, {"id": OLDER_ID}]
            ).encode()
        else:
            body = self._bodies_by_url.get(req.url, self._index_body)
        assert body is not None
        return Ok(
            HttpResponse(
                status=200, url=req.url, headers={}, body=body, elapsed_ms=1
            )
        )


def _ctx(http) -> object:
    return ExecutionContext(
        task_id="test",
        clock=FakeClock(),
        http=http,
        logger=FakeStructuredLogger(),
        cancel=CancellationToken(),
        deadline=None,
        config={},
    )


def _ndjson(rows: list[dict[str, str]]) -> bytes:
    return "".join(json.dumps(row) + "\n" for row in rows).encode()


def _row(url: str, status: str = "200") -> dict[str, str]:
    return {"url": url, "status": status, "timestamp": "20260203081435"}


def _provider(**kwargs) -> WebIndexProvider:
    return WebIndexProvider(**kwargs)


@pytest.mark.asyncio
async def test_index_rows_become_board_roots():
    http = _StubHttp(
        _ndjson(
            [
                _row("https://boards.greenhouse.io/acme"),
                _row("https://boards.greenhouse.io/acme/jobs/123"),
                _row("https://boards.greenhouse.io/acme/jobs/456?utm_source=x"),
                _row("https://boards.greenhouse.io/search"),
                _row("https://boards.greenhouse.io/jobs"),
                _row("https://boards.greenhouse.io/"),
                _row("https://jobs.lever.co/acme"),
                _row("https://boards.greenhouse.io/Beta-Corp"),
                _row("https://boards.greenhouse.io/redirected", status="301"),
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
    # First the crawl listing, then the slice query for the current cursor.
    assert "/collinfo" in http.urls[0]
    assert "boards.greenhouse.io/a*" in http.urls[1]
    assert "limit=" in http.urls[1]


@pytest.mark.asyncio
async def test_collinfo_failure_falls_back_to_cdx():
    """Common Crawl down entirely: the CDX API still serves the slice."""

    class _CollInfoDown(FakeHttpClient):
        def __init__(self) -> None:
            super().__init__()
            self.urls: list[str] = []

        async def send(self, req: HttpRequest):
            self.urls.append(req.url)
            if "/collinfo" in req.url:
                return Err(PortError(code="http.transport", message="down"))
            return Ok(
                HttpResponse(
                    status=200,
                    url=req.url,
                    headers={},
                    body=(
                        json.dumps(
                            [
                                ["original"],
                                ["https://boards.greenhouse.io/acme"],
                            ]
                        ).encode()
                    ),
                    elapsed_ms=1,
                )
            )

    http = _CollInfoDown()
    provider = _provider()

    sources = [
        source async for source in provider.discover(DiscoveryQuery(), _ctx(http))
    ]

    assert [s.url for s in sources] == ["https://boards.greenhouse.io/acme"]
    assert all(s.evidence[0].data["index"] == "cdx" for s in sources)
    assert any("web.archive.org" in url for url in http.urls)


@pytest.mark.asyncio
async def test_both_indexes_down_raises_so_the_slice_is_retried():
    http = _StubHttp(fail=True)
    provider = _provider()

    with pytest.raises(RuntimeError, match="unreachable"):
        _ = [source async for source in provider.discover(DiscoveryQuery(), _ctx(http))]


@pytest.mark.asyncio
async def test_unparseable_index_bodies_fall_back_to_an_older_crawl():
    broken = b"<html>service unavailable</html>"
    http = _StubHttp(
        broken,
        bodies_by_url={
            WebIndexProvider()._query_url(
                OLDER_ID, "boards.greenhouse.io", "a"
            ): _ndjson([_row("https://boards.greenhouse.io/acme")]),
        },
    )
    provider = _provider()

    sources = [
        source async for source in provider.discover(DiscoveryQuery(), _ctx(http))
    ]

    assert [s.url for s in sources] == ["https://boards.greenhouse.io/acme"]
    assert any(OLDER_ID in url for url in http.urls)


@pytest.mark.asyncio
async def test_empty_index_slice_yields_nothing():
    http = _StubHttp(_ndjson([]))
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
    http = _StubHttp(_ndjson([]))
    provider = _provider()

    # Cursor 1 is the second host at the first bucket.
    _ = [
        s
        async for s in provider.discover(DiscoveryQuery(extra={"cursor": 1}), _ctx(http))
    ]

    assert "jobs.ashbyhq.com/a*" in http.urls[1]
