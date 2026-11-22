"""Phenom career-site connector (M4+).

Phenom sites are tenant-specific, so this is a configurable widget adapter
rather than a scraper: the endpoint template, page size and response field
mapping live in :class:`PhenomConfig`. The default contract is the public
``POST {host}/widgets`` endpoint used by Phenom career sites:

- ``{"ddoKey": "refineSearch", ...}`` returns ``refineSearch.data.jobs`` plus
  ``totalHits``;
- ``{"ddoKey": "jobDetail", "jobId": ...}`` returns
  ``jobDetail.data.job`` with the full description.

Custom employer hosts are detected by the career-site connector, which looks
for Phenom markers (``ddoKey``, ``refineSearch``, ``phenompeople``) and routes
the page here. The same host is then used as the widget origin.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig
from app.infrastructure.html.parsing import plain_text
from app.plugins.ats.schema import listing_items

DEFAULT_ENDPOINT = "https://{host}/widgets"

#: Canonical field -> response path. Override per tenant in ``source.limits``.
DEFAULT_FIELD_MAP: dict[str, str] = {
    "id": "jobId",
    "title": "title",
    "location": "location",
    "url": "applyUrl",
    "posted_at": "dateCreated",
    "department": "category",
    "employment_type": "type",
    "description": "description",
}

_MARKERS = ("ddoKey", "refineSearch", "phenompeople")


@dataclass(frozen=True, slots=True)
class PhenomConfig:
    """A tenant's Phenom endpoint and field mapping."""

    endpoint_template: str = DEFAULT_ENDPOINT
    field_map: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_FIELD_MAP))
    page_size: int = 100
    max_pages: int = 5


class PhenomConnector:
    id = "ats.phenom"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        supports_pagination=True,
        requires_rendering=False,
        network_hosts=("phenompeople.com",),
    )

    def __init__(self, config: PhenomConfig | None = None) -> None:
        self._config = config or PhenomConfig()

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        lowered = url.lower()
        matched = "phenompeople.com" in lowered
        if not matched and sniff is not None and sniff.html_excerpt:
            matched = any(marker in sniff.html_excerpt for marker in _MARKERS)
        return MatchResult(
            matched=matched,
            confidence=0.9 if matched else 0.0,
            ats_hint="phenom",
        )

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            host = _host(source.url)
            config = _config_for(source, self._config)
            if not host:
                raise PortError(
                    code="connector.invalid_source",
                    message="phenom source has no host",
                    retryable=False,
                )
            endpoint = config.endpoint_template.format(host=host)
            start = 0
            for _page in range(max(1, config.max_pages)):
                body = _search_body(start, max(1, config.page_size))
                response = await ctx.http.send(
                    HttpRequest(
                        method="POST",
                        url=endpoint,
                        json_body=body,
                        headers={"referer": f"https://{host}/"},
                    )
                )
                if not isinstance(response, Ok):
                    raise PortError(
                        code="connector.fetch_failed", message="phenom list failed"
                    )
                payload = _json(response.value.body)
                result = payload.get("refineSearch")
                if not isinstance(result, Mapping):
                    if start == 0:
                        raise PortError(
                            code="connector.schema_drift",
                            message="phenom: listing response has no 'refineSearch' object",
                        )
                    break
                data = result.get("data")
                jobs = listing_items(
                    data if isinstance(data, Mapping) else {},
                    "jobs",
                    ats="phenom",
                    total_key="totalCount",
                    first_page=start == 0,
                )
                if not jobs:
                    break
                for job in jobs:
                    if not isinstance(job, Mapping):
                        continue
                    job_id = _text(_value(job, config.field_map.get("id", "jobId")))
                    if not job_id:
                        continue
                    yield RawJobRef(
                        source_id=source.id,
                        url=(
                            _text(_value(job, config.field_map.get("url", "applyUrl")))
                            or f"https://{host}/"
                        ),
                        source_posting_id=job_id,
                        updated_at_hint=_text(
                            _value(job, config.field_map.get("posted_at", "dateCreated"))
                        ),
                    )
                start += len(jobs)
                total = result.get("totalHits")
                if len(jobs) < config.page_size or (
                    isinstance(total, int) and start >= total
                ):
                    break

        return gen()

    async def fetch_job(
        self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext
    ) -> Result[RawJob, PortError]:
        host = _host(source.url)
        job_id = ref.source_posting_id
        if not host or not job_id:
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
        config = _config_for(source, self._config)
        endpoint = config.endpoint_template.format(host=host)
        body: dict[str, Any] = _search_body(0, 1)
        body.update({"ddoKey": "jobDetail", "jobId": job_id})
        response = await ctx.http.send(
            HttpRequest(
                method="POST",
                url=endpoint,
                json_body=body,
                headers={"referer": f"https://{host}/"},
            )
        )
        if not isinstance(response, Ok):
            return response
        payload = _json(response.value.body)
        detail = payload.get("jobDetail")
        detail = detail if isinstance(detail, Mapping) else {}
        data = detail.get("data")
        job = data.get("job") if isinstance(data, Mapping) else None
        if not isinstance(job, Mapping):
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
        description = _description(job, config.field_map)
        return Ok(
            RawJob(
                ref=ref,
                title=_text(_value(job, config.field_map.get("title", "title"))),
                body_text=plain_text(description) if description else None,
                body_html=description,
                payload=_payload(job, description, config.field_map),
            )
        )


def _config_for(source: SourceConfig, base: PhenomConfig) -> PhenomConfig:
    raw = source.limits.get("phenom")
    if not isinstance(raw, Mapping):
        return base
    template = raw.get("endpoint_template")
    field_map = raw.get("field_map")
    page_size = raw.get("page_size")
    max_pages = raw.get("max_pages")
    endpoint = template if isinstance(template, str) and template else base.endpoint_template
    return PhenomConfig(
        endpoint_template=endpoint,
        field_map=(
            {str(key): str(value) for key, value in field_map.items()}
            if isinstance(field_map, Mapping)
            else base.field_map
        ),
        page_size=page_size
        if isinstance(page_size, int) and not isinstance(page_size, bool) and page_size > 0
        else base.page_size,
        max_pages=max_pages
        if isinstance(max_pages, int) and not isinstance(max_pages, bool) and max_pages > 0
        else base.max_pages,
    )


def _search_body(start: int, size: int) -> dict[str, Any]:
    return {
        "ddoKey": "refineSearch",
        "eVar": {},
        "sortBy": "",
        "subsearch": "",
        "from": start,
        "jobs": True,
        "counts": True,
        "all_fields": ["category", "country", "state", "city", "type"],
        "size": size,
        "clearAll": False,
        "jdsource": "facets",
        "isSliderEnable": False,
        "pageId": "page11",
        "siteType": "external",
        "keywords": "",
        "global": True,
        "selected_fields": {},
        "sort": [{"order": "desc", "field": "dateCreated"}],
        "locationData": {},
    }


def _description(job: Mapping[str, Any], field_map: Mapping[str, str]) -> str | None:
    keys = [field_map.get("description", "description")]
    keys.extend(
        key
        for key in (
            "description",
            "jobDescription",
            "descriptionTeaser",
            "jobSummary",
            "responsibilities",
            "qualifications",
        )
        if key not in keys
    )
    parts: list[str] = []
    for key in keys:
        text = _text(job.get(key))
        if text and text not in parts:
            parts.append(text)
    return "\n".join(parts) if parts else None


def _payload(
    job: Mapping[str, Any], description: str | None, field_map: Mapping[str, str]
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for canonical, path in (
        ("title", field_map.get("title", "title")),
        ("location", field_map.get("location", "location")),
        ("employmentType", field_map.get("employment_type", "type")),
        ("department", field_map.get("department", "category")),
        ("url", field_map.get("url", "applyUrl")),
        ("posted_at", field_map.get("posted_at", "dateCreated")),
    ):
        text = _text(_value(job, path))
        if text:
            payload[canonical] = text
    locations = _locations(job)
    if locations:
        payload["locations"] = locations
    if description:
        payload["description"] = plain_text(description)
        payload["description_html"] = description
    payload["phenom"] = dict(job)
    return payload


def _locations(job: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("location", "cityStateCountry", "cityState"):
        text = _text(job.get(key))
        if text and text not in out:
            out.append(text)
    rows = job.get("multi_location")
    if isinstance(rows, list):
        for row in rows:
            text = _text(row)
            if text and text not in out:
                out.append(text)
    return out


def _value(row: Mapping[str, Any], path: str) -> object:
    current: object = row
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _host(url: str) -> str:
    return urlsplit(url if "://" in url else f"https://{url}").netloc.lower()


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
