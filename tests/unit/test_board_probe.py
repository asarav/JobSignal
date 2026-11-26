"""Board probe: hiring / live_empty / dead / unknown, and zero confidence."""

from __future__ import annotations

import json

import pytest
from app.application.board_probe import BoardProbe, ProbeResult, ProbeTarget
from app.domain.models.common import Err, Ok, PortError
from app.domain.models.http import HttpRequest, HttpResponse


class _Http:
    """A scripted HttpClient: one body for every request."""

    def __init__(self, status: int = 200, body: bytes = b"{}", url: str | None = None) -> None:
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


class _Raising(_Http):
    async def send(self, req: HttpRequest):
        raise RuntimeError("broken probe")


async def _probe(http: _Http, target: ProbeTarget) -> ProbeResult:
    return await BoardProbe(http).probe(target)


@pytest.mark.asyncio
async def test_hiring_board_reports_its_postings() -> None:
    body = json.dumps({"jobs": [{"id": 1}, {"id": 2}]}).encode()
    result = await _probe(
        _Http(body=body),
        ProbeTarget("ats.greenhouse", "https://boards.greenhouse.io/acme"),
    )
    assert result.state == "hiring"
    assert result.postings == 2


@pytest.mark.asyncio
async def test_confident_zero_is_live_empty() -> None:
    result = await _probe(
        _Http(body=b'{"jobs": []}'),
        ProbeTarget("ats.greenhouse", "https://boards.greenhouse.io/acme"),
    )
    assert result.state == "live_empty"
    assert result.postings == 0


@pytest.mark.asyncio
async def test_gone_board_is_dead() -> None:
    result = await _probe(
        _Http(status=404),
        ProbeTarget("ats.greenhouse", "https://boards.greenhouse.io/acme"),
    )
    assert result.state == "dead"


@pytest.mark.asyncio
async def test_server_error_is_unknown_not_dead() -> None:
    result = await _probe(
        _Http(status=503),
        ProbeTarget("ats.greenhouse", "https://boards.greenhouse.io/acme"),
    )
    assert result.state == "unknown"


@pytest.mark.asyncio
async def test_icims_zero_is_not_trusted() -> None:
    """iCIMS renders postings in an iframe, so a zero is "not observed"."""
    page = b"<html><body>no postings here</body></html>"
    result = await _probe(
        _Http(body=page),
        ProbeTarget("ats.icims", "https://acme.icims.com"),
    )
    assert result.state == "unknown"


@pytest.mark.asyncio
async def test_taleo_zero_is_not_trusted() -> None:
    page = b"<html><body>no postings here</body></html>"
    result = await _probe(
        _Http(body=page),
        ProbeTarget(
            "ats.taleo", "https://acme.taleo.net/careersection/2/jobsearch.ftl?lang=en"
        ),
    )
    assert result.state == "unknown"


@pytest.mark.asyncio
async def test_workday_tenant_redirect_is_dead() -> None:
    result = await _probe(
        _Http(body=b"<html></html>", url="https://www.myworkdayjobs.com/"),
        ProbeTarget("ats.bamboohr", "https://acme.bamboohr.com"),
    )
    assert result.state == "dead"


@pytest.mark.asyncio
async def test_unknown_connector_is_unknown() -> None:
    result = await _probe(
        _Http(), ProbeTarget("ats.phenom", "https://acme.example.com")
    )
    assert result.state == "unknown"


@pytest.mark.asyncio
async def test_a_broken_probe_never_raises() -> None:
    result = await _probe(
        _Raising(),
        ProbeTarget("ats.greenhouse", "https://boards.greenhouse.io/acme"),
    )
    assert result.state == "unknown"


@pytest.mark.asyncio
async def test_transport_failure_is_unknown() -> None:
    class _Offline(_Http):
        async def send(self, req: HttpRequest):
            return Err(PortError(code="network.offline", message="offline", retryable=False))

    result = await _probe(
        _Offline(), ProbeTarget("ats.greenhouse", "https://boards.greenhouse.io/acme")
    )
    assert result.state == "unknown"
