"""BambooHR ATS connector (M4+).

BambooHR hosts a careers page on each customer's subdomain, backed by two
unauthenticated JSON endpoints (the same ones its own widget calls):

- ``GET https://{company}.bamboohr.com/careers/list`` lists the openings;
- ``GET https://{company}.bamboohr.com/careers/{id}/detail`` carries the full
  description, posted date, compensation and the public share URL.

The listing is sparse by design (no description or date), so the connector
fetches one detail per posting.
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

_RESERVED = frozenset({"www", "assets", "api", "static", "cdn", "app", "jobs"})


class BambooHrConnector:
    id = "ats.bamboohr"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        supports_pagination=False,
        requires_rendering=False,
        network_hosts=("bamboohr.com",),
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        matched = _company(url) is not None
        return MatchResult(matched=matched, confidence=0.9 if matched else 0.0, ats_hint="bamboohr")

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            company = _company(source.url, source.ats_tenant_key)
            if company is None:
                raise PortError(
                    code="connector.invalid_source",
                    message="bamboohr source is not a company subdomain",
                    retryable=False,
                )
            response = await ctx.http.send(
                HttpRequest(method="GET", url=f"https://{company}.bamboohr.com/careers/list")
            )
            if not isinstance(response, Ok):
                raise PortError(code="connector.fetch_failed", message="bamboohr list failed")
            data = _json(response.value.body)
            for job in _rows(data):
                job_id = _text(job.get("id"))
                if not job_id:
                    continue
                yield RawJobRef(
                    source_id=source.id,
                    url=f"https://{company}.bamboohr.com/careers/{job_id}",
                    source_posting_id=job_id,
                )

        return gen()

    async def fetch_job(
        self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext
    ) -> Result[RawJob, PortError]:
        company = _company(source.url, source.ats_tenant_key)
        job_id = ref.source_posting_id
        if company is None or not job_id:
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
        response = await ctx.http.send(
            HttpRequest(
                method="GET",
                url=f"https://{company}.bamboohr.com/careers/{job_id}/detail",
            )
        )
        if not isinstance(response, Ok):
            return response
        data = _json(response.value.body)
        result = data.get("result")
        opening = result.get("jobOpening") if isinstance(result, dict) else None
        if not isinstance(opening, dict):
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
        description = _text(opening.get("description"))
        return Ok(
            RawJob(
                ref=ref,
                title=_text(opening.get("jobOpeningName")),
                body_text=plain_text(description) if description else None,
                body_html=description,
                payload=_payload(opening),
            )
        )


def _payload(opening: Mapping[str, Any]) -> dict[str, Any]:
    description = _text(opening.get("description"))
    payload: dict[str, Any] = {}
    if _text(opening.get("jobOpeningName")):
        payload["title"] = opening["jobOpeningName"]
    if description:
        payload["description"] = plain_text(description)
        payload["description_html"] = description
    locations = _locations(opening)
    if locations:
        payload["locations"] = locations
    if _text(opening.get("datePosted")):
        payload["posted_at"] = opening["datePosted"]
    if _text(opening.get("employmentType")):
        payload["employmentType"] = opening["employmentType"]
    if _text(opening.get("employmentStatusLabel")):
        payload["employmentStatus"] = opening["employmentStatusLabel"]
    if opening.get("compensation") not in (None, "", {}):
        payload["salary"] = opening["compensation"]
    if _text(opening.get("jobOpeningShareUrl")):
        payload["url"] = opening["jobOpeningShareUrl"]
    payload["bamboohr"] = dict(opening)
    return payload


def _locations(opening: Mapping[str, Any]) -> list[str]:
    location = opening.get("location")
    city = _text(location.get("city")) if isinstance(location, Mapping) else None
    state = _text(location.get("state")) if isinstance(location, Mapping) else None
    ats = opening.get("atsLocation")
    country = _text(ats.get("country")) if isinstance(ats, Mapping) else None
    parts = [part for part in (city, state, country) if part]
    if parts:
        return [", ".join(parts)]
    return ["Remote"] if opening.get("isRemote") else []


def _company(url: str, tenant_key: str | None = None) -> str | None:
    if tenant_key:
        return tenant_key
    host = urlsplit(url if "://" in url else f"https://{url}").netloc.lower()
    suffix = ".bamboohr.com"
    if not host.endswith(suffix):
        return None
    label = host[: -len(suffix)]
    if not label or "." in label or label in _RESERVED:
        return None
    return label


def _rows(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = data.get("result")
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def _json(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None
