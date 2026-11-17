"""SmartRecruiters ATS connector (M2).

SmartRecruiters exposes a public JSON API:

- ``GET /v1/companies/{company}/postings`` lists the postings,
- ``GET /v1/companies/{company}/postings/{id}`` returns one posting.

Both answers are JSON, so the ``extract.public_endpoint`` strategy reads them.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig

API_BASE = "https://api.smartrecruiters.com/v1/companies"


class SmartRecruitersConnector:
    id = "ats.smartrecruiters"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        network_hosts=("api.smartrecruiters.com",),
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        lowered = url.lower()
        matched = "smartrecruiters.com" in lowered or "jobs.smartrecruiters.com" in lowered
        return MatchResult(
            matched=matched, confidence=0.9 if matched else 0.0, ats_hint="smartrecruiters"
        )

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            company = _company(source)
            url = f"{API_BASE}/{company}/postings?limit=100"
            response = await ctx.http.send(HttpRequest(method="GET", url=url))
            if not isinstance(response, Ok):
                raise PortError(
                    code="connector.fetch_failed", message="smartrecruiters list failed"
                )
            data = _json(response.value.body)
            for posting in data.get("content", []):
                posting_id = posting.get("id")
                yield RawJobRef(
                    source_id=source.id,
                    url=posting.get("ref") or f"{url}/{posting_id}",
                    source_posting_id=str(posting_id) if posting_id is not None else None,
                    updated_at_hint=posting.get("releasedDate"),
                )

        return gen()

    async def fetch_job(self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext) -> Result[RawJob, PortError]:
        if not ref.source_posting_id:
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
        company = _company(source)
        url = f"{API_BASE}/{company}/postings/{ref.source_posting_id}"
        response = await ctx.http.send(HttpRequest(method="GET", url=url))
        if not isinstance(response, Ok):
            return response
        data = _json(response.value.body)
        description = data.get("jobDescription")
        return Ok(
            RawJob(
                ref=ref,
                title=data.get("name"),
                body_text=description,
                body_html=description,
                payload=data,
            )
        )


def _company(source: SourceConfig) -> str:
    if source.ats_tenant_key:
        return source.ats_tenant_key
    parts = [part for part in source.url.rstrip("/").split("/") if part]
    return parts[-1] if parts else ""


def _company_from_url(url: str) -> str:
    parts = [part for part in url.rstrip("/").split("/") if part]
    return parts[-1] if parts else ""


def _json(body: bytes) -> dict:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
