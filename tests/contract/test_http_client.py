"""Contract: every ``HttpClient`` adapter returns domain ``Result`` values.

M0 ships only a fake adapter; the real ``HttpxClient`` arrives with the same
protocol in M1. The contract is intentionally transport-free.
"""

from __future__ import annotations

import pytest
from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.http import HttpRequest, HttpResponse
from app.domain.ports.http import HttpClient
from app.infrastructure.http import policy_client
from app.infrastructure.http.policy_client import PolicyHttpClient, RateLimiter
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


class _RateLimitedTransport(HttpClient):
    """A transport that refuses with 429 until told otherwise."""

    def __init__(self, retry_after: str, then: int | None = None) -> None:
        self.retry_after = retry_after
        self.then = then
        self.sent: list[HttpRequest] = []

    async def send(self, req: HttpRequest) -> Result[HttpResponse, PortError]:
        self.sent.append(req)
        if self.then is not None and len(self.sent) > 1:
            return Ok(HttpResponse(status=200, url=req.url, headers={}, body=b"ok", elapsed_ms=1))
        return Ok(
            HttpResponse(
                status=429,
                url=req.url,
                headers={"retry-after": self.retry_after},
                body=b"slow down",
                elapsed_ms=1,
            )
        )


@pytest.mark.asyncio
async def test_a_429_holds_the_whole_origin_for_its_retry_after():
    """Measured on a 1 req/s host: one request waiting is not enough.

    If only the refusing request waits, every other request to that origin keeps
    knocking through the countdown, and the host answers them 429 as well -- 90%
    of them were recorded as "unreachable" before this was fixed.
    """
    client = PolicyHttpClient(transport=_RateLimitedTransport("30"), max_retries=1)

    # ``respect_robots=False`` keeps this test about the 429 path: with robots
    # on, the robots.txt probe reaches the same refusing transport first.
    result = await client.send(
        HttpRequest(
            method="GET",
            url="https://recruiting.example.com/jobs/1",
            respect_robots=False,
        )
    )

    assert isinstance(result, Err)
    assert result.error.code == "http.rate_limited"
    assert result.error.retryable is True
    assert result.error.details["retry_after_s"] == 30.0
    assert result.error.details["host"] == "recruiting.example.com"
    assert client.rate_limiter.remaining_backoff("recruiting.example.com") > 25.0


@pytest.mark.asyncio
async def test_the_origin_gate_wait_is_what_a_later_request_hits(monkeypatch):
    limiter = RateLimiter(min_interval_s=0.0, burst=2, max_concurrent=2)
    limiter.backoff("slow.example", 5.0)
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(policy_client.asyncio, "sleep", fake_sleep)
    await limiter.acquire("slow.example")

    assert slept and slept[0] > 4.0, "a backed-off origin must make the next request wait"
