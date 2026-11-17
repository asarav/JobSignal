"""Workday ATS connector (M2).

Workday boards are JavaScript applications, but they expose a CxS (candidate
experience services) JSON endpoint that the site itself uses:

- ``POST {host}/wday/cxs/{tenant}/{site}/jobs`` lists the postings,
- ``GET  {host}/wday/cxs/{tenant}/{site}{externalPath}`` returns one posting.

The host, tenant and site are taken from the board URL.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig


class WorkdayConnector:
    id = "ats.workday"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        network_hosts=("myworkdayjobs.com",),
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        lowered = url.lower()
        matched = "myworkdayjobs.com" in lowered or "myworkdaysite.com" in lowered
        return MatchResult(
            matched=matched, confidence=0.9 if matched else 0.0, ats_hint="workday"
        )

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            host, tenant, site = _board(source)
            if not host:
                return
            url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
            response = await ctx.http.send(
                HttpRequest(
                    method="POST",
                    url=url,
                    json_body={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""},
                    headers={"content-type": "application/json"},
                )
            )
            if not isinstance(response, Ok):
                raise PortError(code="connector.fetch_failed", message="workday list failed")
            data = _json(response.value.body)
            for posting in data.get("jobPostings", []):
                path = posting.get("externalPath") or ""
                yield RawJobRef(
                    source_id=source.id,
                    url=f"https://{host}{path}" if path else source.url,
                    source_posting_id=str(posting.get("titleReqId") or posting.get("jobReqId") or path),
                    updated_at_hint=posting.get("postedOn"),
                )

        return gen()

    async def fetch_job(self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext) -> Result[RawJob, PortError]:
        host, tenant, site = _board(source)
        if not host:
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
        path = _external_path(ref.url)
        url = f"https://{host}/wday/cxs/{tenant}/{site}{path}"
        response = await ctx.http.send(HttpRequest(method="GET", url=url))
        if not isinstance(response, Ok):
            return response
        data = _json(response.value.body)
        info = data.get("jobPostingInfo") if isinstance(data.get("jobPostingInfo"), dict) else data
        description = info.get("jobDescription")
        return Ok(
            RawJob(
                ref=ref,
                title=info.get("title"),
                body_text=description,
                body_html=description,
                payload=info,
            )
        )


def _board(source: SourceConfig) -> tuple[str, str, str]:
    """Return ``(host, tenant, site)`` for a Workday board."""
    host, tenant, site = _board_from_url(source.url)
    if source.ats_tenant_key:
        tenant = source.ats_tenant_key
        site = site or tenant
    return host, tenant, site


def _board_from_url(url: str) -> tuple[str, str, str]:
    """Parse host, tenant and site out of a Workday board URL."""
    without_scheme = url.split("://", 1)[-1]
    host, _, path = without_scheme.partition("/")
    segments = [segment for segment in path.split("/") if segment]
    # The first segment is the locale (en-US), then tenant and site.
    if segments and "-" in segments[0] and segments[0].islower():
        segments = segments[1:]
    tenant = segments[0] if segments else ""
    site = segments[1] if len(segments) > 1 else tenant
    return host, tenant, site


def _external_path(url: str) -> str:
    """The path of a posting URL, without the CxS prefix."""
    without_scheme = url.split("://", 1)[-1]
    _, _, path = without_scheme.partition("/")
    return f"/{path}" if path else ""


def _json(body: bytes) -> dict:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
