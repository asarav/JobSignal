"""Web-index discovery provider (M4).

Finds ATS tenants from a **public web URL index** instead of a fixed seed
list: the Internet Archive's CDX API lists URLs a crawler actually fetched,
so the result reflects companies that exist today rather than a snapshot of
yesterday's seed file.  No API key, no account, no user input.

One run asks the index for one ``(ATS host, leading-character bucket)`` pair.
The auto-discovery pass persists and advances that cursor, so each daily run
explores a new slice of the tenant space and the whole space is revisited
after the cursor wraps.

The provider only proposes sources.  The crawler verifies each board, so a
tenant that has gone away costs one failed crawl run instead of polluting the
source list forever.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit

from app.domain.models.common import Evidence, Ok
from app.domain.models.context import ExecutionContext
from app.domain.models.discovery import (
    DiscoveredSource,
    DiscoveryQuery,
    ProviderCapabilities,
)
from app.domain.models.http import HttpRequest

#: Public URL index used to enumerate tenants (query API, no key required).
DEFAULT_INDEX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"

#: (indexed host, connector id) pairs.  Adding an ATS host widens discovery.
DEFAULT_ATS_HOSTS: tuple[tuple[str, str], ...] = (
    ("boards.greenhouse.io", "ats.greenhouse"),
    ("jobs.ashbyhq.com", "ats.ashby"),
    ("jobs.lever.co", "ats.lever"),
    ("jobs.smartrecruiters.com", "ats.smartrecruiters"),
)

#: Leading characters of a tenant key; one bucket is walked per run.
BUCKETS = "abcdefghijklmnopqrstuvwxyz0123456789"

#: First path segments that are site chrome rather than a company board.
RESERVED_SEGMENTS = frozenset(
    {
        "api",
        "assets",
        "embed",
        "images",
        "job",
        "jobs",
        "robots",
        "search",
        "sitemap",
        "static",
        "v1",
    }
)

_TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


class WebIndexProvider:
    """Enumerates ATS tenant boards from a public web URL index."""

    id = "discovery.web_index"
    version = "1.0.0"
    capabilities = ProviderCapabilities(
        offline=False,
        network_hosts=("web.archive.org",),
        requires_opt_in=False,
    )

    def __init__(
        self,
        hosts: Sequence[tuple[str, str]] = DEFAULT_ATS_HOSTS,
        endpoint: str = DEFAULT_INDEX_ENDPOINT,
        *,
        limit: int = 60,
        lookback_days: int = 540,
        timeout_s: float = 45.0,
    ) -> None:
        self._hosts = tuple(hosts) or DEFAULT_ATS_HOSTS
        self._endpoint = endpoint
        self._limit = max(1, limit)
        self._lookback_days = max(1, lookback_days)
        self._timeout_s = timeout_s

    @property
    def rotation_size(self) -> int:
        """Number of runs before the (host, bucket) cursor repeats."""
        return max(1, len(self._hosts) * len(BUCKETS))

    def slice_for(self, cursor: int) -> tuple[str, str, str]:
        """The ``(host, connector id, bucket)`` this cursor walks."""
        index = cursor % self.rotation_size if cursor else 0
        host, connector_id = self._hosts[index % len(self._hosts)]
        bucket = BUCKETS[(index // len(self._hosts)) % len(BUCKETS)]
        return host, connector_id, bucket

    async def discover(
        self, query: DiscoveryQuery, ctx: ExecutionContext
    ) -> AsyncIterator[DiscoveredSource]:
        mixin: Mapping[str, object] = dict(query.extra)
        raw_cursor = mixin.get("cursor", 0)
        cursor = raw_cursor if isinstance(raw_cursor, int) else 0
        host, connector_id, bucket = self.slice_for(cursor)
        index_url = self._index_url(host, bucket)

        response = await ctx.http.send(
            HttpRequest(method="GET", url=index_url, timeout_s=self._timeout_s)
        )
        if not isinstance(response, Ok):
            ctx.logger.warning(
                "discovery.web_index.failed",
                host=host,
                bucket=bucket,
                error=response.error.message,
            )
            return

        observed_at = datetime.now(UTC)
        for original, tenant in self._tenants(response.value.body, host):
            yield DiscoveredSource(
                kind="ats",
                url=f"https://{host}/{tenant}",
                company_hint=tenant.replace("-", " ").title(),
                ats_hint=connector_id,
                confidence=0.6,
                evidence=[
                    Evidence(
                        kind="web_index",
                        description=f"{host}/{bucket} is listed in the public web index",
                        data={"index_url": index_url, "captured_url": original},
                        observed_at=observed_at,
                    )
                ],
            )

    def _index_url(self, host: str, bucket: str) -> str:
        since = (datetime.now(UTC) - timedelta(days=self._lookback_days)).strftime("%Y%m%d")
        params = [
            ("url", f"{host}/{bucket}"),
            ("matchType", "prefix"),
            ("fl", "original"),
            ("collapse", "urlkey"),
            ("filter", "statuscode:200"),
            ("from", since),
            ("limit", str(self._limit)),
            ("output", "json"),
        ]
        return f"{self._endpoint}?{urlencode(params)}"

    def _tenants(self, body: bytes, host: str) -> list[tuple[str, str]]:
        """Reduce an index response to ``(captured url, tenant key)`` pairs."""
        try:
            rows = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return []
        if not isinstance(rows, list):
            return []
        found: list[tuple[str, str]] = []
        seen: set[str] = set()
        for row in rows[1:]:
            if not isinstance(row, list) or not row or not isinstance(row[0], str):
                continue
            original = row[0]
            tenant = _tenant_of(original, host)
            if tenant is None or tenant in seen:
                continue
            seen.add(tenant)
            found.append((original, tenant))
        return found


def _tenant_of(url: str, host: str) -> str | None:
    """The ATS tenant key of an indexed URL, or ``None`` for site chrome."""
    parts = urlsplit(url)
    if parts.netloc.lower() != host:
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments:
        return None
    tenant = segments[0].lower()
    if tenant in RESERVED_SEGMENTS or not _TENANT_RE.match(tenant):
        return None
    return tenant
