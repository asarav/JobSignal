"""Ashby ATS connector (M1)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig


class AshbyConnector:
    id = "ats.ashby"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        network_hosts=("api.ashbyhq.com", "jobs.ashbyhq.com"),
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        lowered = url.lower()
        matched = "ashbyhq.com" in lowered or "ashby" in lowered
        return MatchResult(matched=matched, confidence=0.9 if matched else 0.0, ats_hint="ashby")

    async def list_jobs(self, source: SourceConfig, ctx: ExecutionContext) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            slug = source.ats_tenant_key or source.url.rstrip("/").split("/")[-1]
            url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
            response = await ctx.http.send(HttpRequest(method="GET", url=url))
            if not isinstance(response, Ok):
                raise PortError(code="connector.fetch_failed", message="ashby list failed")
            data = json.loads(response.value.body.decode("utf-8", errors="replace"))
            for job in data.get("jobs", []):
                yield RawJobRef(
                    source_id=source.id,
                    url=job.get("jobUrl") or f"{url}/{job.get('id')}",
                    source_posting_id=str(job.get("id")) if job.get("id") is not None else None,
                    updated_at_hint=job.get("publishedAt"),
                )

        return gen()

    async def fetch_job(self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext) -> Result[RawJob, PortError]:
        return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
