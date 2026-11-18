"""Minimal httpx adapter for M0 diagnostics.

This is deliberately *not* a crawler: no robots policy, retries, rate limits or
caching. M1 introduces ``PolicyHttpClient`` around this adapter.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from app.application.config import HttpConfig
from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.http import HttpRequest, HttpResponse


class HttpxClient:
    def __init__(self, config: HttpConfig) -> None:
        self._config = config

    async def send(self, req: HttpRequest) -> Result[HttpResponse, PortError]:
        scheme = urlparse(req.url).scheme.lower()
        if scheme not in {"http", "https"}:
            return Err(
                PortError(
                    code="validation.invalid_params",
                    message="only http/https URLs are supported",
                )
            )
        timeout = min(req.timeout_s, self._config.max_timeout_s)
        max_bytes = min(req.max_bytes, self._config.max_bytes)
        headers = {"User-Agent": self._config.user_agent, **dict(req.headers)}
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                max_redirects=self._config.max_redirects,
            ) as client:
                response = await client.request(
                    req.method,
                    req.url,
                    headers=headers,
                    json=req.json_body if req.json_body is not None else None,
                )
        except httpx.TimeoutException as exc:
            return Err(
                PortError(code="http.timeout", message=str(exc), retryable=True)
            )
        except httpx.HTTPError as exc:
            return Err(
                PortError(code="network.offline", message=str(exc), retryable=True)
            )
        body = response.content
        if len(body) > max_bytes:
            return Err(
                PortError(
                    code="http.too_large",
                    message=f"response exceeds {max_bytes} bytes",
                    details={"max_bytes": max_bytes},
                )
            )
        return Ok(
            HttpResponse(
                status=response.status_code,
                url=str(response.url),
                headers={k.lower(): v for k, v in response.headers.items()},
                body=body,
                elapsed_ms=int(response.elapsed.total_seconds() * 1000)
                if response.elapsed
                else 0,
            )
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "HttpxClient()"


def make_http_client(cfg: HttpConfig) -> Any:
    return HttpxClient(cfg)
