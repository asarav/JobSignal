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
    kwargs.setdefault("cdx_retry_delay_s", 0.0)
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
    host_count = 5

    first_host, first_ats, first_bucket = provider.slice_for(0)
    assert first_host == "boards.greenhouse.io"
    assert first_ats == "ats.greenhouse"
    assert first_bucket == BUCKETS[0]

    # The next hosts at the same bucket come before the next bucket.
    assert provider.slice_for(1)[0] == "job-boards.greenhouse.io"
    assert provider.slice_for(2)[0] == "jobs.ashbyhq.com"
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

    assert "job-boards.greenhouse.io/a*" in http.urls[1]


@pytest.mark.asyncio
async def test_one_pass_walks_several_slices_in_parallel():
    http = _StubHttp(_ndjson([]))
    provider = _provider()

    _ = [
        s
        async for s in provider.discover(
            DiscoveryQuery(extra={"cursor": 0, "slices": 3}), _ctx(http)
        )
    ]

    # collinfo once (shared across slices), then one Common Crawl query per
    # slice (the empty answer also triggers one CDX attempt per slice while
    # the pass-level budget of four lasts).
    slice_urls = [u for u in http.urls[1:] if "collinfo" not in u]
    assert len(slice_urls) == 6
    assert sum(1 for u in http.urls if "collinfo" in u) == 1
    assert any("boards.greenhouse.io/a*" in u for u in slice_urls)
    assert any("job-boards.greenhouse.io/a*" in u for u in slice_urls)
    assert any("jobs.ashbyhq.com/a*" in u for u in slice_urls)


@pytest.mark.asyncio
async def test_same_tenant_on_two_greenhouse_hosts_is_deduped():
    """boards. and job-boards. host the same tenant; one board is proposed."""
    bodies = {
        WebIndexProvider()._query_url(COLL_ID, "boards.greenhouse.io", "a"): _ndjson(
            [_row("https://boards.greenhouse.io/acme")]
        ),
        WebIndexProvider()._query_url(
            COLL_ID, "job-boards.greenhouse.io", "a"
        ): _ndjson([_row("https://job-boards.greenhouse.io/acme")]),
    }
    http = _StubHttp(collinfo_body=json.dumps([{"id": COLL_ID}]).encode(), bodies_by_url=bodies)
    provider = _provider()

    sources = [
        s async for s in provider.discover(
            DiscoveryQuery(extra={"cursor": 0, "slices": 2}), _ctx(http)
        )
    ]

    # The first host in the rotation wins; the duplicate is dropped.
    assert [s.url for s in sources] == ["https://boards.greenhouse.io/acme"]


@pytest.mark.asyncio
async def test_cdx_fallbacks_are_capped_per_pass():
    """A burst of dead Common Crawl slices must not become a CDX crawl."""

    class _IndexDown(FakeHttpClient):
        def __init__(self) -> None:
            super().__init__()
            self.urls: list[str] = []

        async def send(self, req: HttpRequest):
            self.urls.append(req.url)
            if "/collinfo" in req.url:
                body = json.dumps([{"id": COLL_ID}]).encode()
            elif "web.archive.org" in req.url:
                body = json.dumps(
                    [["original"], ["https://boards.greenhouse.io/acme"]]
                ).encode()
            else:
                return Err(PortError(code="http.transport", message="down"))
            return Ok(
                HttpResponse(
                    status=200, url=req.url, headers={}, body=body, elapsed_ms=1
                )
            )

    http = _IndexDown()
    provider = _provider()

    _ = [s async for s in provider.discover(
        DiscoveryQuery(extra={"cursor": 0, "slices": 6}), _ctx(http)
    )]

    cdx_calls = [u for u in http.urls if "web.archive.org" in u]
    assert len(cdx_calls) == 4  # MAX_CDX_FALLBACKS, not one per dead slice


@pytest.mark.asyncio
async def test_cdx_asks_for_a_whole_bucket_and_retries_once():
    """The CDX fallback uses the measured high row limit, not a small sample."""

    class _CdxFlaky(FakeHttpClient):
        def __init__(self) -> None:
            super().__init__()
            self.urls: list[str] = []
            self.cdx_attempts = 0

        async def send(self, req: HttpRequest):
            self.urls.append(req.url)
            if "/collinfo" in req.url:
                return Err(PortError(code="http.transport", message="down"))
            self.cdx_attempts += 1
            if self.cdx_attempts == 1:
                return Err(PortError(code="http.gateway", message="temporarily offline"))
            return Ok(
                HttpResponse(
                    status=200,
                    url=req.url,
                    headers={},
                    body=json.dumps(
                        [["original"], ["https://boards.greenhouse.io/acme"]]
                    ).encode(),
                    elapsed_ms=1,
                )
            )

    http = _CdxFlaky()
    provider = _provider()

    sources = [
        source async for source in provider.discover(DiscoveryQuery(), _ctx(http))
    ]

    assert [s.url for s in sources] == ["https://boards.greenhouse.io/acme"]
    assert http.cdx_attempts == 2  # one 503, one retry
    assert all("limit=10000" in url for url in http.urls if "web.archive.org" in url)

