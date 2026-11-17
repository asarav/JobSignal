"""Policy-aware HTTP client: robots, rate limits, retries, caps.

Wraps a raw :class:`HttpClient` (the transport) and enforces the crawl policy
defined by ``AppConfig.http`` and per-source limits. M1 ships a conservative
implementation; the full robots parser and distributed rate limiter arrive with
the crawler hardening in M8.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.http import HttpRequest, HttpResponse
from app.domain.ports.http import HttpClient


@dataclass
class RateLimiter:
    min_interval_s: float = 1.0
    _last: dict[str, float] = field(default_factory=dict)

    async def acquire(self, host: str) -> None:
        now = time.monotonic()
        last = self._last.get(host, 0.0)
        wait = self.min_interval_s - (now - last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last[host] = time.monotonic()


@dataclass
class PolicyHttpClient:
    transport: HttpClient
    rate_limiter: RateLimiter = field(default_factory=RateLimiter)
    max_retries: int = 2
    retry_backoff_s: float = 1.0
    user_agent: str = "JobSignal/0.1 (+local)"

    async def send(self, req: HttpRequest) -> Result[HttpResponse, PortError]:
        host = urlparse(req.url).netloc
        await self.rate_limiter.acquire(host)

        headers = dict(req.headers)
        headers.setdefault("User-Agent", self.user_agent)

        last_error: PortError | None = None
        for attempt in range(self.max_retries + 1):
            result = await self.transport.send(
                HttpRequest(
                    method=req.method,
                    url=req.url,
                    headers=headers,
                    json_body=req.json_body,
                    timeout_s=req.timeout_s,
                    max_bytes=req.max_bytes,
                    respect_robots=req.respect_robots,
                )
            )
            if isinstance(result, Ok):
                if result.value.status < 500:
                    return result
                last_error = PortError(
                    code="http.server_error",
                    message=f"HTTP {result.value.status}",
                    retryable=True,
                )
            else:
                last_error = result.error if hasattr(result, "error") else PortError(
                    code="http.transport", message="transport failure", retryable=True
                )
            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_backoff_s * (2**attempt))
        if last_error is None:
            last_error = PortError(code="http.failed", message="request failed", retryable=False)
        return Err(last_error)
