"""Minimal httpx adapter for diagnostics and the crawl transport.

This is deliberately *not* the crawler's policy layer: no robots, retries or
rate limits (``PolicyHttpClient`` adds those). What it does own is the wire:
one request at a time, a size cap, and redirects followed by hand so every hop
is re-checked against :mod:`app.domain.services.url_guard` before it is
requested (§16, §19.5.8).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from app.application.config import HttpConfig
from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.http import HttpRequest, HttpResponse
from app.domain.services.url_guard import unsafe_url_reason

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class HttpxClient:
    def __init__(
        self,
        config: HttpConfig,
        transport: httpx.AsyncBaseTransport | None = None,
        allow_private_hosts: bool = False,
    ) -> None:
        self._config = config
        #: Injected by tests; ``None`` in production uses httpx's own transport.
        self._transport = transport
        #: Test-only: fixture servers on loopback. No setting enables this.
        self._allow_private_hosts = allow_private_hosts

    async def send(self, req: HttpRequest) -> Result[HttpResponse, PortError]:
        if not self._allow_private_hosts:
            reason = unsafe_url_reason(req.url)
            if reason is not None:
                return Err(
                    PortError(
                        code="validation.invalid_params",
                        message=f"refusing to fetch {req.url}: {reason}",
                    )
                )
        timeout = min(req.timeout_s, self._config.max_timeout_s)
        max_bytes = min(req.max_bytes, self._config.max_bytes)
        headers = {"User-Agent": self._config.user_agent, **dict(req.headers)}
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                followed = await self._follow(client, req, headers)
        except httpx.TimeoutException as exc:
            return Err(
                PortError(code="http.timeout", message=str(exc), retryable=True)
            )
        except httpx.HTTPError as exc:
            return Err(
                PortError(code="network.offline", message=str(exc), retryable=True)
            )
        if isinstance(followed, Err):
            return followed
        response = followed.value
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
                elapsed_ms=_elapsed_ms(response),
            )
        )

    async def _follow(
        self,
        client: httpx.AsyncClient,
        req: HttpRequest,
        headers: dict[str, str],
    ) -> Result[httpx.Response, PortError]:
        """Request the URL, following redirects only to acceptable targets."""
        url = req.url
        method = req.method
        limit = int(self._config.max_redirects)
        for hop in range(limit + 1):
            response = await client.request(
                method,
                url,
                headers=headers,
                json=req.json_body if req.json_body is not None else None,
            )
            location = response.headers.get("location")
            if response.status_code not in _REDIRECT_STATUSES or not location:
                return Ok(response)
            if hop >= limit:
                return Err(
                    PortError(
                        code="http.redirect_loop",
                        message=f"more than {limit} redirects",
                        retryable=False,
                    )
                )
            target = urljoin(url, location)
            if not self._allow_private_hosts:
                reason = unsafe_url_reason(target)
                if reason is not None:
                    return Err(
                        PortError(
                            code="network.unsafe_redirect",
                            message=f"refusing redirect to {target}: {reason}",
                            retryable=False,
                        )
                    )
            url = target
            if response.status_code == 303:
                method = "GET"
        # Unreachable: the loop returns on its last iteration.
        return Err(
            PortError(code="http.redirect_loop", message="redirect loop", retryable=False)
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "HttpxClient()"


def _elapsed_ms(response: httpx.Response) -> int:
    try:
        elapsed = response.elapsed
    except RuntimeError:  # a mock response has no timing
        return 0
    return int(elapsed.total_seconds() * 1000) if elapsed else 0


def make_http_client(cfg: HttpConfig) -> Any:
    return HttpxClient(cfg)
