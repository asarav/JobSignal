"""Greenhouse ATS connector (M1)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from urllib.parse import urlparse

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig


class GreenhouseConnector:
    id = "ats.greenhouse"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        supports_pagination=False,
        requires_rendering=False,
        network_hosts=("boards-api.greenhouse.io", "boards.greenhouse.io"),
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        lowered = url.lower()
        matched = "greenhouse.io" in lowered or "greenhouse" in lowered
        return MatchResult(
            matched=matched, confidence=0.9 if matched else 0.0, ats_hint="greenhouse"
        )

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            token = source.ats_tenant_key or _tenant_from_url(source.url)
            url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
            response = await ctx.http.send(HttpRequest(method="GET", url=url))
            if not isinstance(response, Ok):
                raise PortError(code="connector.fetch_failed", message="greenhouse list failed")
            data = json.loads(response.value.body.decode("utf-8", errors="replace"))
            for job in data.get("jobs", []):
                yield RawJobRef(
                    source_id=source.id,
                    url=job.get("absolute_url") or f"{url}/{job.get('id')}",
                    source_posting_id=str(job.get("id")) if job.get("id") is not None else None,
                    updated_at_hint=job.get("updated_at"),
                )

        return gen()

    async def fetch_job(
        self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext
    ) -> Result[RawJob, PortError]:
        token = _tenant_from_url(ref.url) or (source.ats_tenant_key or "")
        if token and ref.source_posting_id:
            detail_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{ref.source_posting_id}"
            response = await ctx.http.send(HttpRequest(method="GET", url=detail_url))
            if isinstance(response, Ok):
                try:
                    data = json.loads(response.value.body.decode("utf-8", errors="replace"))
                except ValueError:
                    data = {}
                if data.get("title"):
                    return Ok(
                        RawJob(
                            ref=ref,
                            title=data.get("title"),
                            body_text=data.get("content"),
                            body_html=data.get("content"),
                            payload=data,
                        )
                    )
            # Fall back to the board list, which carries the title + location.
            list_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
            response = await ctx.http.send(HttpRequest(method="GET", url=list_url))
            if isinstance(response, Ok):
                try:
                    data = json.loads(response.value.body.decode("utf-8", errors="replace"))
                except ValueError:
                    data = {}
                for job in data.get("jobs", []):
                    if str(job.get("id")) == str(ref.source_posting_id):
                        return Ok(
                            RawJob(
                                ref=ref,
                                title=job.get("title"),
                                body_text=job.get("content"),
                                body_html=job.get("content"),
                                payload=job,
                            )
                        )
        return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))


def _tenant_from_url(url: str) -> str:
    """Extract the board token from a Greenhouse URL.

    Greenhouse boards live under several hosts (``boards.greenhouse.io``,
    ``job-boards.greenhouse.io`` and ``boards-api.greenhouse.io``); the token
    is always the first path segment after the host.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "greenhouse.io" not in host:
        return ""
    segments = [part for part in parsed.path.split("/") if part]
    if not segments:
        return ""
    # boards-api.greenhouse.io/v1/boards/<token>/...
    if "boards" in segments:
        idx = segments.index("boards")
        if idx + 1 < len(segments):
            return segments[idx + 1]
    # boards.greenhouse.io/<token>/... or job-boards.greenhouse.io/<token>/...
    return segments[0]
