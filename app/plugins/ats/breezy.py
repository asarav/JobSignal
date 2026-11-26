"""Breezy HR connector.

A Breezy board is a tenant subdomain (``{tenant}.breezy.hr``) whose career page
loads every published position from one public document:

- ``GET https://{tenant}.breezy.hr/json`` answers a bare array with ``id``,
  ``friendly_id``, ``name``, ``url``, ``published_date``, ``type``,
  ``location`` and ``department`` for every posting, unpaginated;
- the posting page (``/p/{friendly_id}``) is server-rendered and carries the
  description in its ``.description`` block.

The feed has **no descriptions**, so the connector pays one fetch per posting.
That cost is why boards are crawled through the normal source budget rather
than probed in bulk: the listing is cheap, the details are not.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import urlsplit

from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig
from app.plugins.ats.posting_check import invalid_posting, posting_problem

HOST_SUFFIX = ".breezy.hr"
APP_HOST = "app.breezy.hr"
_RESERVED = frozenset({"www", "app", "api", "assets", "static", "cdn", "help", "support"})


class BreezyConnector:
    id = "ats.breezy"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        supports_pagination=False,
        requires_rendering=False,
        network_hosts=("breezy.hr",),
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        matched = _tenant(url) is not None
        return MatchResult(matched=matched, confidence=0.9 if matched else 0.0, ats_hint="breezy")

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            tenant = _tenant(source.url, source.ats_tenant_key)
            if tenant is None:
                raise PortError(
                    code="connector.invalid_source",
                    message="breezy source is not a board subdomain",
                    retryable=False,
                )
            url = f"https://{tenant}{HOST_SUFFIX}/json"
            response = await ctx.http.send(HttpRequest(method="GET", url=url))
            if not isinstance(response, Ok):
                raise PortError(code="connector.fetch_failed", message="breezy list failed")
            for item in _rows(response.value.body):
                posting_id = _text(item.get("friendly_id")) or _text(item.get("id"))
                if not posting_id:
                    continue
                yield RawJobRef(
                    source_id=source.id,
                    url=_text(item.get("url"))
                    or f"https://{tenant}{HOST_SUFFIX}/p/{posting_id}",
                    source_posting_id=posting_id,
                    updated_at_hint=_text(item.get("published_date")),
                )

        return gen()

    async def fetch_job(
        self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext
    ) -> Result[RawJob, PortError]:
        response = await ctx.http.send(HttpRequest(method="GET", url=ref.url))
        if not isinstance(response, Ok):
            return response
        html = response.value.body.decode("utf-8", errors="replace")
        problem = posting_problem(html)
        if problem is not None:
            return Err(invalid_posting(self.id, ref.url, problem))
        return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=html, payload={}))


def _rows(body: bytes) -> list[Mapping[str, Any]]:
    """The positions in a ``/json`` answer.

    A board that answers with an object instead of the documented array has
    changed shape; that must fail the crawl rather than read as empty.
    """
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise PortError(
            code="connector.schema_drift",
            message=f"breezy: listing is not JSON ({exc})",
        ) from exc
    if isinstance(data, Mapping):
        rows = data.get("positions")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, Mapping)]
        raise PortError(
            code="connector.schema_drift",
            message="breezy: listing is not an array (endpoint schema changed?)",
        )
    if not isinstance(data, list):
        raise PortError(
            code="connector.schema_drift",
            message="breezy: listing is not an array (endpoint schema changed?)",
        )
    return [row for row in data if isinstance(row, Mapping)]


def _tenant(url: str, tenant_key: str | None = None) -> str | None:
    """The board's tenant label, or ``None`` for the platform's own site."""
    if tenant_key:
        return tenant_key.strip().lower() or None
    parts = urlsplit(url if "://" in url else f"https://{url}")
    host = parts.netloc.lower()
    if host == APP_HOST:
        segments = [segment for segment in parts.path.split("/") if segment]
        tenant = segments[0].lower() if segments else ""
        return tenant if tenant and tenant not in _RESERVED else None
    if not host.endswith(HOST_SUFFIX):
        return None
    tenant = host[: -len(HOST_SUFFIX)]
    if not tenant or "." in tenant or tenant in _RESERVED:
        return None
    return tenant


def _text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None
