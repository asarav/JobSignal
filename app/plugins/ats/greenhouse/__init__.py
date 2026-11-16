"""Greenhouse ATS connector (M1)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

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
        self, ref: RawJobRef, ctx: ExecutionContext
    ) -> Result[RawJob, PortError]:
        if ref.source_posting_id:
            token = _tenant_from_url(ref.url) or ""
            if token:
                url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{ref.source_posting_id}"
                response = await ctx.http.send(HttpRequest(method="GET", url=url))
                if isinstance(response, Ok):
                    try:
                        data = json.loads(response.value.body.decode("utf-8", errors="replace"))
                    except (ValueError, json.JSONDecodeError):
                        data = {}
                    return Ok(
                        RawJob(
                            ref=ref,
                            title=data.get("title"),
                            body_text=data.get("content"),
                            body_html=data.get("content"),
                            payload=data,
                        )
                    )
        return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))


def _tenant_from_url(url: str) -> str:
    parts = url.rstrip("/").split("/")
    # https://boards.greenhouse.io/<token>/jobs/<id>
    if "boards.greenhouse.io" in url:
        try:
            idx = parts.index("boards.greenhouse.io")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        except ValueError:
            pass
    # https://boards-api.greenhouse.io/v1/boards/<token>/jobs/<id>
    for i, part in enumerate(parts):
        if part == "boards" and i + 1 < len(parts):
            return parts[i + 1]
    return ""
