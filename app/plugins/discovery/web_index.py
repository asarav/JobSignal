"""Web-index discovery provider (M4).

Finds ATS tenants from a **public web URL index** instead of a fixed seed
list: Common Crawl's index server lists URLs its crawls actually fetched, so
the result reflects companies that exist today rather than a snapshot of
yesterday's seed file.  No API key, no account, no user input.

Common Crawl answers in seconds, but its gateway currently times out on the
largest ATS hosts, so an unserved slice falls back to the Internet Archive's
CDX API (the same kind of public index, slower: tens of seconds per query).

One run asks the index for one ``(ATS host, leading-character bucket)`` pair.
The auto-discovery pass persists and advances that cursor, so each daily run
explores a new slice of the tenant space and the whole space is revisited
after the cursor wraps.  If a pass cannot read the index, the cursor is held
and the same slice is retried next pass instead of being skipped.

The provider only proposes sources.  The crawler verifies each board, so a
tenant that has gone away costs one failed crawl run instead of polluting the
source list forever.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from urllib.parse import quote, urlencode, urlsplit

from app.domain.models.common import Evidence, Ok
from app.domain.models.context import ExecutionContext
from app.domain.models.discovery import (
    DiscoveredSource,
    DiscoveryQuery,
    ProviderCapabilities,
)
from app.domain.models.http import HttpRequest

#: Public URL index used to enumerate tenants (query API, no key required).
DEFAULT_INDEX_ENDPOINT = "https://index.commoncrawl.org"

#: Lists the available crawl indexes; the newest one is queried first.
COLLINFO_PATH = "/collinfo.json"

#: How many indexes to try, newest first, before falling back to the CDX API.
MAX_CRAWL_FALLBACKS = 2

#: Internet Archive CDX API; the fallback index for slices Common Crawl
#: cannot currently serve (its gateway times out on the largest ATS hosts).
CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"

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
    version = "1.1.0"
    capabilities = ProviderCapabilities(
        offline=False,
        network_hosts=("index.commoncrawl.org", "web.archive.org"),
        requires_opt_in=False,
    )

    def __init__(
        self,
        hosts: Sequence[tuple[str, str]] = DEFAULT_ATS_HOSTS,
        endpoint: str = DEFAULT_INDEX_ENDPOINT,
        *,
        limit: int = 100,
        timeout_s: float = 45.0,
        cdx_timeout_s: float = 90.0,
    ) -> None:
        self._hosts = tuple(hosts) or DEFAULT_ATS_HOSTS
        self._endpoint = endpoint.rstrip("/")
        self._limit = max(1, limit)
        self._timeout_s = timeout_s
        self._cdx_timeout_s = cdx_timeout_s
        self._crawl_ids: list[str] | None = None

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

        # Common Crawl answers in seconds; the Internet Archive CDX API needs
        # a minute but serves the hosts whose CC partitions are unavailable.
        cc_rows = await self._common_crawl_rows(ctx, host, bucket)
        rows: list[tuple[str, str, str]] | None
        source = "common_crawl"
        if not cc_rows:
            # No Common Crawl rows for this slice (or it failed): an empty
            # answer there often means poor coverage, not an empty bucket.
            cdx_rows = await self._cdx_rows(ctx, host, bucket)
            if cdx_rows is not None:
                rows, source = cdx_rows, "cdx"
            elif cc_rows is not None:
                rows = cc_rows  # empty, but an index answered
            else:
                rows = None
        else:
            rows = cc_rows
        if rows is None:
            # Both indexes are unreachable: raise so the pass holds the
            # rotation cursor and retries this slice next time.
            ctx.logger.warning(
                "discovery.web_index.unreachable", host=host, bucket=bucket
            )
            raise RuntimeError(f"web index unreachable for {host}/{bucket}")

        observed_at = datetime.now(UTC)
        for tenant, original, timestamp in rows:
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
                        data={
                            "index": source,
                            "captured_url": original,
                            "captured_timestamp": timestamp,
                        },
                        observed_at=observed_at,
                    )
                ],
            )

    async def _common_crawl_rows(
        self, ctx: ExecutionContext, host: str, bucket: str
    ) -> list[tuple[str, str, str]] | None:
        """Rows from Common Crawl's index; ``None`` if it cannot serve the slice."""
        crawl_ids = await self._crawl_ids_of(ctx)
        if crawl_ids is None:
            return None
        for crawl_id in crawl_ids[:MAX_CRAWL_FALLBACKS]:
            rows = await self._query(ctx, crawl_id, host, bucket)
            if rows is not None:
                return rows
        return None

    async def _cdx_rows(
        self, ctx: ExecutionContext, host: str, bucket: str
    ) -> list[tuple[str, str, str]] | None:
        """Rows from the Internet Archive CDX API; ``None`` if unreachable.

        The query deliberately carries no ``filter``/``from`` clauses: they
        make the scan significantly slower, and dead or redirected URLs are
        fine here because the crawler verifies every board.
        """
        params = [
            ("url", f"{host}/{bucket}"),
            ("matchType", "prefix"),
            ("fl", "original"),
            ("collapse", "urlkey"),
            ("limit", str(self._limit)),
            ("output", "json"),
        ]
        url = f"{CDX_ENDPOINT}?{urlencode(params)}"
        response = await ctx.http.send(
            HttpRequest(method="GET", url=url, timeout_s=self._cdx_timeout_s)
        )
        if not isinstance(response, Ok):
            ctx.logger.warning(
                "discovery.web_index.cdx_failed",
                host=host,
                bucket=bucket,
                error=response.error.message,
            )
            return None
        rows = self._cdx_parse(response.value.body, host)
        if rows is None:
            ctx.logger.warning(
                "discovery.web_index.cdx_unparseable", host=host, bucket=bucket
            )
        return rows

    def _cdx_parse(
        self, body: bytes, host: str
    ) -> list[tuple[str, str, str]] | None:
        try:
            table = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return None
        if not isinstance(table, list):
            return None
        rows: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for record in table[1:]:
            if not isinstance(record, list) or not record:
                continue
            original = record[0]
            if not isinstance(original, str):
                continue
            tenant = _tenant_of(original, host)
            if tenant is None or tenant in seen:
                continue
            seen.add(tenant)
            rows.append((tenant, original, ""))
        return rows

    async def _crawl_ids_of(self, ctx: ExecutionContext) -> list[str] | None:
        """The crawl indexes to query, newest first (cached per instance)."""
        if self._crawl_ids is not None:
            return self._crawl_ids
        response = await ctx.http.send(
            HttpRequest(
                method="GET",
                url=f"{self._endpoint}{COLLINFO_PATH}",
                timeout_s=self._timeout_s,
            )
        )
        if not isinstance(response, Ok):
            ctx.logger.warning(
                "discovery.web_index.collinfo_failed",
                error=response.error.message,
            )
            return None
        try:
            colls = json.loads(response.value.body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            ctx.logger.warning("discovery.web_index.collinfo_unparseable")
            return None
        if not isinstance(colls, list):
            ctx.logger.warning("discovery.web_index.collinfo_unparseable")
            return None
        ids = [
            coll["id"]
            for coll in colls
            if isinstance(coll, dict) and isinstance(coll.get("id"), str)
        ]
        if not ids:
            ctx.logger.warning("discovery.web_index.no_crawl_indexes")
            return None
        self._crawl_ids = ids
        return ids

    async def _query(
        self, ctx: ExecutionContext, crawl_id: str, host: str, bucket: str
    ) -> list[tuple[str, str, str]] | None:
        """Ask one index for the slice; ``None`` means it failed to answer."""
        url = self._query_url(crawl_id, host, bucket)
        response = await ctx.http.send(
            HttpRequest(method="GET", url=url, timeout_s=self._timeout_s)
        )
        if not isinstance(response, Ok):
            ctx.logger.warning(
                "discovery.web_index.failed",
                host=host,
                bucket=bucket,
                index_id=crawl_id,
                error=response.error.message,
            )
            return None
        rows = self._rows(response.value.body, host)
        if rows is None:
            ctx.logger.warning(
                "discovery.web_index.unparseable",
                host=host,
                bucket=bucket,
                index_id=crawl_id,
            )
        return rows

    def _query_url(self, crawl_id: str, host: str, bucket: str) -> str:
        url_param = quote(f"{host}/{bucket}*", safe="/*")
        return f"{self._endpoint}/{crawl_id}-index?url={url_param}&output=json&limit={self._limit}"

    def _rows(
        self, body: bytes, host: str
    ) -> list[tuple[str, str, str]] | None:
        """Reduce an index response to ``(tenant, captured url, timestamp)``.

        ``None`` marks an unusable body (an error page, not NDJSON) so the
        caller can fall back to an older index.
        """
        rows: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for line in body.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                return None
            if not isinstance(row, dict):
                continue
            url = row.get("url")
            if row.get("status") != "200" or not isinstance(url, str):
                continue
            tenant = _tenant_of(url, host)
            if tenant is None or tenant in seen:
                continue
            seen.add(tenant)
            timestamp = row.get("timestamp")
            rows.append(
                (tenant, url, timestamp if isinstance(timestamp, str) else "")
            )
        return rows


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
