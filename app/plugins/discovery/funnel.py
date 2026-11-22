"""Job-URL harvesting from the public web index (M6).

Board discovery already walks the index for tenant URLs.  The same index rows
also contain posting URLs (``boards.greenhouse.io/acme/jobs/123``), so this
provider turns those rows into posting candidates without another request:
``discovery.funnel`` asks the same ``(host, bucket)`` slices and reuses the
pass-scoped index cache that board discovery populates (see
``WebIndexProvider._cache``).

Harvesting is not crawling.  A candidate is a URL only; the funnel verifies
each one with a single fetch before it becomes a job, and it never feeds the
``not_seen``/removal logic that full listings own.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from urllib.parse import urlsplit

from app.domain.models.common import Evidence
from app.domain.models.context import ExecutionContext
from app.domain.models.discovery import DiscoveredPosting, DiscoveredSource, DiscoveryQuery
from app.plugins.discovery.web_index import (
    MAX_CDX_FALLBACKS,
    PASS_BUDGET_S,
    SLICE_CONCURRENCY,
    SLICE_STAGGER_S,
    WebIndexProvider,
)

#: Board URL and the path shape of a posting for each fixed-host ATS.
#: ``Greenhouse`` uses ``/<tenant>/jobs/<id>``; the job-board hosts (Ashby,
#: Lever) and SmartRecruiters put ``/<tenant>/<posting>`` directly.
_POSTING_SHAPES: dict[str, str] = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.ashbyhq.com": "direct",
    "jobs.lever.co": "direct",
    "jobs.smartrecruiters.com": "direct",
}


class FunnelProvider(WebIndexProvider):
    """Harvests posting URLs from the same index slices as board discovery."""

    #: The provider registry keys plugins by their manifest id; inheriting the
    #: board provider's id would make the two indistinguishable in logs and
    #: event payloads.
    id = "discovery.funnel"
    version = "1.1.0"

    async def discover(
        self, query: DiscoveryQuery, ctx: ExecutionContext
    ) -> AsyncIterator[DiscoveredSource]:
        """Board discovery is the other provider's job; this one yields none."""
        return
        yield  # pragma: no cover - keeps this an async generator

    async def discover_postings(
        self, query: DiscoveryQuery, ctx: ExecutionContext
    ) -> AsyncIterator[DiscoveredPosting]:
        mixin = dict(query.extra)
        raw_cursor = mixin.get("cursor", 0)
        cursor = raw_cursor if isinstance(raw_cursor, int) else 0
        raw_slices = mixin.get("slices", 1)
        slices = raw_slices if isinstance(raw_slices, int) and raw_slices > 0 else 1

        semaphore = asyncio.Semaphore(SLICE_CONCURRENCY)
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
            if not isinstance(outcome, BaseException) and outcome[1] is not None
        )
        if reachable == 0:
            ctx.logger.warning("discovery.funnel.unreachable", slices=slices, cursor=cursor)
            raise RuntimeError(f"web index unreachable for funnel cursor {cursor}")

        observed_at = datetime.now(UTC)
        seen: set[str] = set()
        for outcome in outcomes:
            if isinstance(outcome, BaseException) or outcome[1] is None:
                continue
            offset, rows = outcome
            host, connector_id, bucket = self.slice_for(cursor + offset)
            backend = rows[0][3] if rows else "common_crawl"
            for tenant, original, timestamp, row_backend in rows:
                posting = _posting_of(original, host, tenant, connector_id)
                if posting is None or posting.url in seen:
                    continue
                seen.add(posting.url)
                yield DiscoveredPosting(
                    url=posting.url,
                    board_url=posting.board_url,
                    connector_id=connector_id,
                    source_posting_id=posting.source_posting_id,
                    company_hint=tenant.replace("-", " ").title(),
                    tenant_key=tenant,
                    confidence=0.6,
                    evidence=(
                        Evidence(
                            kind="web_index",
                            description=(
                                f"{host}/{bucket} lists a posting for {tenant} "
                                "in the public web index"
                            ),
                            data={
                                "index": row_backend or backend,
                                "captured_url": original,
                                "captured_timestamp": timestamp,
                            },
                            observed_at=observed_at,
                        ),
                    ),
                )


def _posting_of(
    url: str, host: str, tenant: str, connector_id: str
) -> DiscoveredPosting | None:
    """Turn one indexed URL into a posting candidate, or ``None``."""
    parts = urlsplit(url)
    if parts.netloc.lower() != host:
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments or segments[0].lower() != tenant:
        return None
    shape = _POSTING_SHAPES.get(host, "direct")
    posting_id: str | None = None
    if shape == "greenhouse":
        if len(segments) != 3 or segments[1].lower() != "jobs":
            return None
        posting_id = segments[2]
    else:
        if len(segments) != 2:
            return None
        posting_id = segments[1]
    if not posting_id or not _looks_like_posting_id(posting_id):
        return None
    clean = f"https://{host}/{tenant}/{segments[1]}/{posting_id}" if shape == "greenhouse" else f"https://{host}/{tenant}/{posting_id}"
    return DiscoveredPosting(
        url=clean,
        board_url=f"https://{host}/{tenant}",
        connector_id=connector_id,
        source_posting_id=posting_id,
        tenant_key=tenant,
    )


def _looks_like_posting_id(value: str) -> bool:
    if not 2 <= len(value) <= 80:
        return False
    return any(char.isdigit() for char in value) or "-" in value or "_" in value
