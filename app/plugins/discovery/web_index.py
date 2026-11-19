"""Web-index discovery provider (M4).

Finds ATS tenants from a **public web URL index** instead of a fixed seed
list: Common Crawl's index server lists URLs its crawls actually fetched, so
the result reflects companies that exist today rather than a snapshot of
yesterday's seed file.  No API key, no account, no user input.

Common Crawl answers in seconds, but its gateway currently times out on the
largest ATS hosts, so an unserved slice falls back to the Internet Archive's
CDX API (the same kind of public index, slower: seconds to tens of seconds
per query).  Both backends are asked for a large block of rows per slice
(``limit=10,000``) rather than a small sample: the gateway serves that in
~13 s and one request then covers a whole leading-character bucket.

One pass asks the index for several ``(ATS host, leading-character bucket)``
pairs (``slices`` from the pass), so a daily run covers a meaningful stretch
of the tenant space; the auto-discovery pass persists and advances that
cursor, so coverage grows every run and the whole space is revisited after
the cursor wraps.  If a pass cannot read the index at all, the cursor is
held and the same slices are retried next pass instead of being skipped.

Because the same tenant can be indexed on more than one host (Greenhouse's
``boards.`` and ``job-boards.`` subdomains, say), a pass dedupes candidates
by ``(connector id, tenant key)`` and yields the first host it saw.

The provider only proposes sources.  The crawler verifies each board, so a
tenant that has gone away costs one failed crawl run instead of polluting the
source list forever.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
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
    ("job-boards.greenhouse.io", "ats.greenhouse"),
    ("jobs.ashbyhq.com", "ats.ashby"),
    ("jobs.lever.co", "ats.lever"),
    ("jobs.smartrecruiters.com", "ats.smartrecruiters"),
)

#: How many index queries run concurrently inside one pass. The archive
#: gateways throttle bursts, so this stays low and slice starts are staggered.
SLICE_CONCURRENCY = 3

#: Seconds between slice starts; paces the fan-out without serializing it.
SLICE_STAGGER_S = 1.0

#: Rows requested per Common Crawl query (a fast, columnar index server).
CC_LIMIT = 200

#: Rows requested per CDX query.  Measured against the Internet Archive
#: gateway: ``limit=10000`` answers in ~13 s (23 s for 5,000) and returns a
#: whole first-character bucket -- e.g. 10,001 rows for
#: ``boards.greenhouse.io/a``, which reduce to 102 distinct tenants.  Small
#: limits sample a fraction of a percent of a bucket and were why an earlier
#: seed list came out undersized.
CDX_LIMIT = 10000

#: CDX fallback queries one pass may spend.  Each takes seconds to tens of
#: seconds even at the high row limit, so a run of empty-or-failed Common
#: Crawl slices must not turn the pass into a serial CDX crawl; the unserved
#: slices are simply skipped this pass and revisited when the cursor wraps.
MAX_CDX_FALLBACKS = 4

#: Wall-clock budget for the whole pass (the discovery call timeout is
#: 300 s; the provider stops starting expensive fallbacks well before it).
PASS_BUDGET_S = 240.0

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
    version = "1.2.0"
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
        limit: int = 200,
        cdx_limit: int = CDX_LIMIT,
        timeout_s: float = 45.0,
        cdx_timeout_s: float = 60.0,
        cdx_retry_delay_s: float = 5.0,
    ) -> None:
        self._hosts = tuple(hosts) or DEFAULT_ATS_HOSTS
        self._endpoint = endpoint.rstrip("/")
        self._limit = max(1, limit)
        self._cdx_limit = max(1, cdx_limit)
        self._timeout_s = timeout_s
        self._cdx_timeout_s = cdx_timeout_s
        #: Pause before the one CDX retry; injectable so tests never sleep.
        self._cdx_retry_delay_s = max(0.0, cdx_retry_delay_s)
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
        raw_slices = mixin.get("slices", 1)
        slices = raw_slices if isinstance(raw_slices, int) and raw_slices > 0 else 1

        # Walk several slices per pass, in parallel, so one daily run covers
        # a meaningful stretch of the tenant space instead of one thin
        # `(host, bucket)` column of it.  Shared pass state: `seen` dedupes
        # `(connector id, tenant)` across hosts (Greenhouse boards live on
        # two subdomains), `shared["cdx_left"]` caps the slow CDX fallbacks,
        # `shared["deadline"]` stops late slices from starting expensive
        # fallbacks, and `shared["crawl_ids"]` shares one collinfo answer
        # across all slices instead of one request per slice.
        semaphore = asyncio.Semaphore(SLICE_CONCURRENCY)
        seen: set[tuple[str, str]] = set()
        shared: dict[str, object] = {
            "cdx_left": MAX_CDX_FALLBACKS,
            "deadline": time.monotonic() + PASS_BUDGET_S,
            "crawl_ids": None,
        }

        async def fetch(offset: int) -> tuple[int, list[tuple[str, str, str, str]] | None]:
            await asyncio.sleep(SLICE_STAGGER_S * offset)
            async with semaphore:
                return offset, await self._slice_rows(ctx, cursor + offset, shared)

        outcomes = await asyncio.gather(
            *(fetch(offset) for offset in range(slices)), return_exceptions=True
        )
        reachable = sum(
            1
            for outcome in outcomes
            if not isinstance(outcome, BaseException)
            and outcome[1] is not None  # type: ignore[misc]
        )
        if reachable == 0:
            # Every backend for every slice failed: raise so the pass holds
            # the rotation cursor and retries this range next time.
            ctx.logger.warning(
                "discovery.web_index.unreachable", slices=slices, cursor=cursor
            )
            raise RuntimeError(f"web index unreachable for cursor {cursor}")

        observed_at = datetime.now(UTC)
        for outcome in outcomes:
            if isinstance(outcome, BaseException) or outcome[1] is None:
                continue
            offset, rows = outcome
            host, connector_id, bucket = self.slice_for(cursor + offset)
            for tenant, original, timestamp, backend in rows:
                key = (connector_id, tenant)
                if key in seen:
                    continue
                seen.add(key)
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
                                "index": backend,
                                "captured_url": original,
                                "captured_timestamp": timestamp,
                            },
                            observed_at=observed_at,
                        )
                    ],
                )

    async def _slice_rows(
        self, ctx: ExecutionContext, cursor: int, shared: dict[str, object]
    ) -> list[tuple[str, str, str, str]] | None:
        """Rows for one slice; ``None`` when both index backends fail.

        Each row is ``(tenant, captured url, timestamp, index backend)``.
        ``shared`` is mutable pass state (see ``discover``): the slow CDX
        fallback may only be spent ``MAX_CDX_FALLBACKS`` times per pass and
        not at all once the pass deadline has passed.
        """
        host, _connector_id, bucket = self.slice_for(cursor)
        # Common Crawl answers in seconds; the Internet Archive CDX API needs
        # a minute but serves the hosts whose CC partitions are unavailable.
        cc_rows = await self._common_crawl_rows(ctx, host, bucket, shared)
        rows: list[tuple[str, str, str]] | None
        backend = "common_crawl"
        if not cc_rows:
            # No Common Crawl rows for this slice (or it failed): an empty
            # answer there often means poor coverage, not an empty bucket.
            # But CDX is slow and the pass is budgeted, so only spend the
            # fallback while there is budget left for it.
            if (
                shared["cdx_left"] <= 0  # type: ignore[operator]
                or time.monotonic() >= shared["deadline"]  # type: ignore[operator]
            ):
                rows = cc_rows  # empty, or None if CC also failed
            else:
                shared["cdx_left"] -= 1  # type: ignore[operator]
                cdx_rows = await self._cdx_rows(ctx, host, bucket, shared)
                if cdx_rows is not None:
                    rows = cdx_rows
                    backend = "cdx"
                else:
                    rows = cc_rows  # empty, but an index answered
        else:
            rows = cc_rows
        return [(tenant, url, ts, backend) for tenant, url, ts in rows]

    async def _common_crawl_rows(
        self,
        ctx: ExecutionContext,
        host: str,
        bucket: str,
        shared: dict[str, object],
    ) -> list[tuple[str, str, str]] | None:
        """Rows from Common Crawl's index; ``None`` if it cannot serve the slice.

        The collinfo answer is shared across slices of one pass (one HTTP
        request instead of one per slice), and a second crawl index is only
        tried while the pass still has time budget left.
        """
        crawl_ids = shared.get("crawl_ids")
        if crawl_ids is None:
            task = shared.get("crawl_ids_task")
            if not isinstance(task, asyncio.Task):
                task = asyncio.create_task(self._crawl_ids_of(ctx))
                shared["crawl_ids_task"] = task
            crawl_ids = await task
            shared["crawl_ids"] = crawl_ids
        if not isinstance(crawl_ids, list) or not crawl_ids:
            return None
        backoffs = crawl_ids[:MAX_CRAWL_FALLBACKS]
        for position, crawl_id in enumerate(backoffs):
            if position and time.monotonic() >= shared["deadline"]:  # type: ignore[operator]
                return None
            rows = await self._query(ctx, crawl_id, host, bucket)
            if rows is not None:
                return rows
        return None

    async def _cdx_rows(
        self,
        ctx: ExecutionContext,
        host: str,
        bucket: str,
        shared: dict[str, object],
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
            ("limit", str(self._cdx_limit)),
            ("output", "json"),
        ]
        url = f"{CDX_ENDPOINT}?{urlencode(params)}"
        # The gateway answers 503/504 under bursts.  One retry covers that,
        # but only while the pass still has budget: a late fallback must not
        # sleep past the pass deadline.
        for attempt in range(2):
            response = await ctx.http.send(
                HttpRequest(method="GET", url=url, timeout_s=self._cdx_timeout_s)
            )
            if isinstance(response, Ok):
                rows = self._cdx_parse(response.value.body, host)
                if rows is None:
                    ctx.logger.warning(
                        "discovery.web_index.cdx_unparseable", host=host, bucket=bucket
                    )
                return rows
            ctx.logger.warning(
                "discovery.web_index.cdx_failed",
                host=host,
                bucket=bucket,
                error=response.error.message,
                attempt=attempt + 1,
            )
            if attempt == 0:
                if (
                    time.monotonic() + self._cdx_retry_delay_s
                    >= shared["deadline"]  # type: ignore[operator]
                ):
                    break
                await asyncio.sleep(self._cdx_retry_delay_s)
        return None

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
