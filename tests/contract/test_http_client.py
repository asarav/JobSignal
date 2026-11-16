"""Contract: every ``HttpClient`` adapter returns domain ``Result`` values.

M0 ships only a fake adapter; the real ``HttpxClient`` arrives with the same
protocol in M1. The contract is intentionally transport-free.
"""

from __future__ import annotations

import pytest
from app.domain.models.common import Err, Ok, PortError
from app.domain.models.http import HttpRequest, HttpResponse
from app.domain.ports.http import HttpClient
from tests.support.fakes import FakeHttpClient


@pytest.mark.asyncio
async def test_fake_http_client_satisfies_protocol():
    client: HttpClient = FakeHttpClient()
    result = await client.send(HttpRequest(method="GET", url="https://example.com"))

    assert isinstance(result, (Ok, Err))
    assert isinstance(result.value, HttpResponse) if isinstance(result, Ok) else True


@pytest.mark.asyncio
async def test_fake_http_client_records_requests_without_network():
    client = FakeHttpClient()
    await client.send(HttpRequest(method="GET", url="https://a.example"))
    await client.send(HttpRequest(method="GET", url="https://b.example"))
    assert [str(req.url) for req in client.sent] == [
        "https://a.example",
        "https://b.example",
    ]


@pytest.mark.asyncio
async def test_errors_are_port_errors():
    client = FakeHttpClient()
    client.block_socket = True
    result = await client.send(HttpRequest(method="GET", url="https://example.com"))
    assert isinstance(result, Err)
    assert isinstance(result.error, PortError)
    assert result.error.code == "network.offline"
