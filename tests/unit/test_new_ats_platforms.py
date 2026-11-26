"""Rippling, Personio and Breezy: parse, identity, discovery and probing."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import scripts.build_seed as build_seed
from app.application.board_probe import BoardProbe, ProbeResult, ProbeTarget
from app.domain.models.common import Ok
from app.domain.models.http import HttpRequest, HttpResponse
from app.domain.services.board_identity import board_identity
from app.plugins.discovery.domain_index import _candidate
from scripts.build_seed import Board, _rippling_board, _subdomain_board

PERSONIO_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position><id>1</id><name>One</name></position>
  <position><id>2</id><name>Two</name></position>
</workzag-jobs>
"""


class _Http:
    """A scripted HttpClient: one status/body for every request."""

    def __init__(self, status: int = 200, body: bytes = b"", url: str | None = None) -> None:
        self.status = status
        self.body = body
        self.url = url
        self.urls: list[str] = []

    async def send(self, req: HttpRequest):
        self.urls.append(req.url)
        return Ok(
            HttpResponse(
                status=self.status,
                url=self.url or req.url,
                headers={},
                body=self.body,
                elapsed_ms=1,
            )
        )


# ------------------------------------------------------------------ seed builder


def test_rippling_board_uses_the_slug_and_the_jobs_path() -> None:
    board = _rippling_board("https://ats.rippling.com/acme-corp/jobs/84d388b6")

    assert board == Board(
        "ats.rippling",
        "acme-corp",
        "https://ats.rippling.com/acme-corp/jobs",
        tenant_key="acme-corp",
    )
    assert board is not None
    assert board.dedupe_key == board_identity(
        "ats.rippling", "https://ats.rippling.com/acme-corp/jobs"
    )


def test_rippling_platform_paths_are_not_boards() -> None:
    for url in (
        "https://ats.rippling.com/api/v2/board/acme/jobs",
        "https://ats.rippling.com/jobs",
        "https://www.rippling.com/careers",
    ):
        assert _rippling_board(url) is None, url


def test_personio_board_accepts_both_career_domains() -> None:
    deutschland = _subdomain_board("ats.personio", "https://acme.jobs.personio.de/job/1")
    international = _subdomain_board("ats.personio", "https://acme.jobs.personio.com/")

    assert deutschland == Board(
        "ats.personio", "acme", "https://acme.jobs.personio.de/", tenant_key="acme"
    )
    assert international == Board(
        "ats.personio", "acme", "https://acme.jobs.personio.com/", tenant_key="acme"
    )


def test_personio_and_breezy_walk_are_configured() -> None:
    assert build_seed.SUBDOMAIN_ATS["ats.personio"]["roots"] == (
        "jobs.personio.de",
        "jobs.personio.com",
    )
    assert build_seed.SUBDOMAIN_ALIAS_DOMAINS["ats.personio"] == ("jobs.personio.com",)
    assert build_seed.SUBDOMAIN_ATS["ats.breezy"]["domain"] == "breezy.hr"
    assert build_seed.PREFIX_BOARDS["ats.rippling"]["parse"] is _rippling_board


def test_breezy_board_is_the_tenant_subdomain() -> None:
    assert _subdomain_board("ats.breezy", "https://acme.breezy.hr/p/1-engineer") == Board(
        "ats.breezy", "acme", "https://acme.breezy.hr", tenant_key="acme"
    )


def test_rippling_verify_counts_the_items_list() -> None:
    page = {"items": [{"id": "a"}, {"id": "b"}], "totalItems": 137}
    assert build_seed._count_jobs("ats.rippling", page) == 137
    # A response without a total falls back to the page it did return.
    assert build_seed._count_jobs("ats.rippling", {"items": [{"id": "a"}]}) == 1
    assert build_seed._count_jobs("ats.rippling", {}) == 0


def test_personio_verify_counts_positions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PERSONIO_XML)

    board = Board("ats.personio", "acme", "https://acme.jobs.personio.de/")

    async def run() -> int | None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await build_seed._verify_personio(client, board, asyncio.Semaphore(1))

    assert asyncio.run(run()) == 2


def test_personio_verify_rejects_a_200_that_is_not_the_feed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html="<html><body>maintenance</body></html>")

    board = Board("ats.personio", "acme", "https://acme.jobs.personio.de/")

    async def run() -> int | None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await build_seed._verify_personio(client, board, asyncio.Semaphore(1))

    assert asyncio.run(run()) is None


def test_personio_probe_does_not_read_an_error_page_as_empty() -> None:
    async def check() -> ProbeResult:
        return await _probe(
            _Http(body=b"<html><body>maintenance</body></html>"),
            ProbeTarget("ats.personio", "https://acme.jobs.personio.de/"),
        )

    result = asyncio.run(check())
    assert result.state == "unknown"


def test_personio_verify_treats_an_unknown_tenant_as_dead() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"nope")

    board = Board("ats.personio", "gone", "https://gone.jobs.personio.de/")

    async def run() -> int | None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await build_seed._verify_personio(client, board, asyncio.Semaphore(1))

    assert asyncio.run(run()) is None


def test_breezy_verify_counts_positions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"id": "1", "friendly_id": "1-a"}, {"id": "2", "friendly_id": "2-b"}]
        )

    board = Board("ats.breezy", "acme", "https://acme.breezy.hr")

    async def run() -> int | None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await build_seed._verify_breezy(client, board, asyncio.Semaphore(1))

    assert asyncio.run(run()) == 2


# ---------------------------------------------------------------- board identity


def test_each_new_platform_has_one_identity_per_board() -> None:
    assert board_identity(
        "ats.personio", "https://acme.jobs.personio.com/job/9"
    ) == ("ats.personio", "acme")
    assert board_identity("ats.breezy", "https://acme.breezy.hr/p/1-x") == (
        "ats.breezy",
        "acme",
    )
    assert board_identity(
        "ats.rippling", "https://ats.rippling.com/acme/jobs/84d388b6"
    ) == ("ats.rippling", "acme")


# ------------------------------------------------------------- domain discovery


def test_domain_index_rebuilds_the_new_boards() -> None:
    personio = _candidate("ats.personio", "https://acme.jobs.personio.com/job/1")
    breezy = _candidate("ats.breezy", "https://acme.breezy.hr/p/1-engineer")

    assert personio is not None
    assert personio.url == "https://acme.jobs.personio.com/"
    assert personio.tenant == "acme"
    assert breezy is not None
    assert breezy.url == "https://acme.breezy.hr"
    assert breezy.tenant == "acme"


def test_domain_index_ignores_the_platforms_own_hosts() -> None:
    assert _candidate("ats.breezy", "https://breezy.hr/pricing") is None
    assert _candidate("ats.personio", "https://www.jobs.personio.de/") is None


# ------------------------------------------------------------------------ probes


async def _probe(http: _Http, target: ProbeTarget) -> ProbeResult:
    return await BoardProbe(http).probe(target)


@pytest.mark.asyncio
async def test_rippling_probe_counts_the_api_listing() -> None:
    body = json.dumps({"items": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}).encode()
    result = await _probe(
        _Http(body=body), ProbeTarget("ats.rippling", "https://ats.rippling.com/acme/jobs")
    )

    assert result.state == "hiring"
    assert result.postings == 3


@pytest.mark.asyncio
async def test_personio_probe_reads_the_xml_feed() -> None:
    result = await _probe(
        _Http(body=PERSONIO_XML),
        ProbeTarget("ats.personio", "https://acme.jobs.personio.de/"),
    )

    assert result.state == "hiring"
    assert result.postings == 2


@pytest.mark.asyncio
async def test_personio_probe_zero_is_trusted() -> None:
    result = await _probe(
        _Http(body=b"<workzag-jobs></workzag-jobs>"),
        ProbeTarget("ats.personio", "https://acme.jobs.personio.de/"),
    )

    assert result.state == "live_empty"


@pytest.mark.asyncio
async def test_breezy_probe_reports_a_gone_board_as_dead() -> None:
    result = await _probe(
        _Http(status=404), ProbeTarget("ats.breezy", "https://gone.breezy.hr")
    )

    assert result.state == "dead"


@pytest.mark.asyncio
async def test_breezy_probe_counts_the_json_array() -> None:
    body = json.dumps([{"friendly_id": "1-a"}]).encode()
    result = await _probe(
        _Http(body=body), ProbeTarget("ats.breezy", "https://acme.breezy.hr")
    )

    assert result.state == "hiring"
    assert result.postings == 1
