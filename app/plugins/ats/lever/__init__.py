"""Lever ATS connector (M1)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig


class LeverConnector:
    id = "ats.lever"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        network_hosts=("api.lever.co", "jobs.lever.co"),
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        lowered = url.lower()
        matched = "lever.co" in lowered or "lever" in lowered
        return MatchResult(matched=matched, confidence=0.9 if matched else 0.0, ats_hint="lever")

    async def list_jobs(self, source: SourceConfig, ctx: ExecutionContext) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            company = source.ats_tenant_key or source.url.rstrip("/").split("/")[-1]
            url = f"https://api.lever.co/v0/postings/{company}?mode=json"
            response = await ctx.http.send(HttpRequest(method="GET", url=url))
            if not isinstance(response, Ok):
                raise PortError(code="connector.fetch_failed", message="lever list failed")
            data = json.loads(response.value.body.decode("utf-8", errors="replace"))
            items = data if isinstance(data, list) else data.get("data", [])
            for job in items:
                yield RawJobRef(
                    source_id=source.id,
                    url=job.get("hostedUrl") or f"{url}/{job.get('id')}",
                    source_posting_id=str(job.get("id")) if job.get("id") is not None else None,
                    updated_at_hint=job.get("createdAt"),
                )

        return gen()

    async def fetch_job(self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext) -> Result[RawJob, PortError]:
        return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
