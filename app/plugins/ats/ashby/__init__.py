"""Ashby ATS connector (M1/M6).

Ashby's public posting API returns the whole board, descriptions included, so
``fetch_job`` reads that one endpoint and selects the requested posting.  It
used to return an empty ``RawJob``, which made every posting the funnel verified
from an index URL a title-less, description-less job.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import urlsplit

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig
from app.infrastructure.html.parsing import plain_text
from app.plugins.ats.schema import listing_items


class AshbyConnector:
    id = "ats.ashby"
    version = "1.1.0"
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
            for job in listing_items(data, "jobs", ats="ashby"):
                yield RawJobRef(
                    source_id=source.id,
                    url=job.get("jobUrl") or f"{url}/{job.get('id')}",
                    source_posting_id=str(job.get("id")) if job.get("id") is not None else None,
                    updated_at_hint=job.get("publishedAt"),
                )

        return gen()

    async def fetch_job(self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext) -> Result[RawJob, PortError]:
        slug = (
            source.ats_tenant_key
            or _tenant_from_url(ref.url)
            or _tenant_from_url(source.url)
        )
        if not slug:
            raise PortError(
                code="connector.invalid_ref",
                message="ashby: posting URL carries no board slug",
            )
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        response = await ctx.http.send(HttpRequest(method="GET", url=url))
        if not isinstance(response, Ok):
            raise PortError(code="connector.fetch_failed", message="ashby detail failed")
        try:
            data = json.loads(response.value.body.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise PortError(
                code="connector.schema_drift",
                message="ashby: detail response was not JSON",
            ) from exc
        for job in listing_items(data, "jobs", ats="ashby"):
            if not isinstance(job, Mapping) or not _is_same_posting(job, ref):
                continue
            body_text, body_html = _body_of(job)
            payload = dict(job)
            payload["title"] = job.get("title")
            payload["description"] = body_text
            location = job.get("location")
            if isinstance(location, str) and location.strip():
                payload["location"] = location.strip()
            return Ok(
                RawJob(
                    ref=ref,
                    title=job.get("title"),
                    body_text=body_text,
                    body_html=body_html,
                    payload=payload,
                )
            )
        raise PortError(
            code="connector.not_found",
            message="ashby: posting is no longer on the board",
        )


def _is_same_posting(job: Mapping[str, Any], ref: RawJobRef) -> bool:
    if ref.source_posting_id and str(job.get("id")) == str(ref.source_posting_id):
        return True
    job_url = job.get("jobUrl")
    if isinstance(job_url, str) and job_url:
        return job_url.rstrip("/").lower() == ref.url.rstrip("/").lower()
    return False


def _body_of(job: Mapping[str, Any]) -> tuple[str, str]:
    plain = str(job.get("descriptionPlain") or "").strip()
    html = str(job.get("descriptionHtml") or "").strip()
    if not plain and html:
        plain = plain_text(html)
    return plain, html


def _tenant_from_url(url: str) -> str:
    """First path segment of an Ashby board URL (``jobs.ashbyhq.com/<org>``)."""
    segments = [part for part in urlsplit(url).path.split("/") if part]
    return segments[0] if segments else ""
