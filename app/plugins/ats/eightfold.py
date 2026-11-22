"""Eightfold AI connector (M4+).

Eightfold powers hundreds of career sites, either on ``{tenant}.eightfold.ai``
or on an employer-owned host (Netflix, for example, uses
``explore.jobs.netflix.net``). The public board calls:

- ``GET {host}/api/apply/v2/jobs?domain={domain}&start={n}&num={size}`` for the
  listing (``positions`` plus ``count``);
- ``GET {host}/api/apply/v2/jobs/{id}?domain={domain}`` for one posting,
  including its HTML description.

The API requires the employer's web domain, which is not always the tenant
label. It is taken from the source's tenant key when that looks like a domain,
then from the board page (``"domain": ...``), and only then guessed as
``{tenant}.com``. Custom hosts that embed Eightfold are routed here by the
career-site connector's wrapper detection.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from html import unescape
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig
from app.infrastructure.html.parsing import plain_text
from app.plugins.ats.schema import listing_items

PAGE_SIZE = 100
#: The public endpoint caps each response at ten positions even when asked for
#: more, so a board with hundreds of jobs needs several pages. Twenty pages
#: keeps one crawl inside the connector timeout while covering 200 postings.
MAX_PAGES = 20
API_PATH = "/api/apply/v2/jobs"
_DOMAIN_RE = re.compile(r'"domain"\s*:\s*"([A-Za-z0-9.-]+)"')
_DOMAIN_QUERY_RE = re.compile(r"[?&]domain=([A-Za-z0-9.-]+)")
_JOB_ID_RE = re.compile(r"/api/apply/v2/jobs/([^/?#]+)")


class EightfoldConnector:
    id = "ats.eightfold"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        supports_pagination=True,
        requires_rendering=False,
        network_hosts=("eightfold.ai",),
    )

    def __init__(self) -> None:
        self._domain_cache: dict[str, str] = {}

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        host = _host(url)
        matched = (
            (host.endswith(".eightfold.ai") and host != "www.eightfold.ai")
            or "/api/apply/v2/jobs" in url.lower()
        )
        return MatchResult(
            matched=matched,
            confidence=0.9 if matched else 0.0,
            ats_hint="eightfold",
        )

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            host = _host(source.url)
            if not host:
                raise PortError(
                    code="connector.invalid_source",
                    message="eightfold source has no host",
                    retryable=False,
                )
            domain = await self._resolve_domain(source, ctx)
            if not domain:
                raise PortError(
                    code="connector.invalid_source",
                    message="eightfold source has no employer domain",
                    retryable=False,
                )
            start = 0
            for _page in range(MAX_PAGES):
                params = urlencode({"domain": domain, "start": start, "num": PAGE_SIZE})
                response = await ctx.http.send(
                    HttpRequest(method="GET", url=f"https://{host}{API_PATH}?{params}")
                )
                if not isinstance(response, Ok):
                    raise PortError(
                        code="connector.fetch_failed", message="eightfold list failed"
                    )
                data = _json(response.value.body)
                positions = listing_items(
                    data, "positions", ats="eightfold", total_key="count", first_page=start == 0
                )
                if not positions:
                    break
                for job in positions:
                    if not isinstance(job, Mapping):
                        continue
                    job_id = _id_text(job.get("id"))
                    if not job_id:
                        continue
                    yield RawJobRef(
                        source_id=source.id,
                        url=(
                            _text(job.get("canonicalPositionUrl"))
                            or f"https://{host}/careers/job/{job_id}"
                        ),
                        source_posting_id=job_id,
                        updated_at_hint=_timestamp(job.get("t_update") or job.get("t_create")),
                    )
                start += len(positions)
                total = data.get("count")
                if isinstance(total, int) and start >= total:
                    break
                if not isinstance(total, int) and len(positions) < PAGE_SIZE:
                    break

        return gen()

    async def _resolve_domain(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> str | None:
        cached = self._domain_cache.get(source.url)
        if cached:
            return cached
        domain = await _domain(source, ctx)
        if domain:
            self._domain_cache[source.url] = domain
        return domain

    async def fetch_job(
        self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext
    ) -> Result[RawJob, PortError]:
        host = _host(source.url)
        job_id = ref.source_posting_id or _job_id(ref.url)
        if not host or not job_id:
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
        domain = await self._resolve_domain(source, ctx)
        query = f"?{urlencode({'domain': domain})}" if domain else ""
        response = await ctx.http.send(
            HttpRequest(method="GET", url=f"https://{host}{API_PATH}/{job_id}{query}")
        )
        if not isinstance(response, Ok):
            return response
        data = _json(response.value.body)
        if not data:
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
        description = _text(data.get("job_description"))
        payload = _payload(data)
        return Ok(
            RawJob(
                ref=ref,
                title=_text(data.get("name")),
                body_text=plain_text(description) if description else None,
                body_html=description,
                payload=payload,
            )
        )


async def _domain(source: SourceConfig, ctx: ExecutionContext) -> str | None:
    """Resolve the employer web domain the Eightfold API expects."""
    key = (source.ats_tenant_key or "").strip()
    if "." in key:
        return key
    parsed = urlsplit(source.url if "://" in source.url else f"https://{source.url}")
    query_domain = parse_qs(parsed.query).get("domain", [""])[0]
    if query_domain:
        return query_domain
    response = await ctx.http.send(HttpRequest(method="GET", url=source.url))
    if isinstance(response, Ok):
        html = unescape(response.value.body.decode("utf-8", errors="replace"))
        match = _DOMAIN_RE.search(html) or _DOMAIN_QUERY_RE.search(html)
        if match:
            return match.group(1)
        group = re.search(r'_EF_GROUP_ID\s*=\s*"([A-Za-z0-9.-]+)"', html)
        if group:
            return group.group(1)
    host = parsed.netloc.lower()
    if host.endswith(".eightfold.ai"):
        tenant = host[: -len(".eightfold.ai")]
        return f"{tenant}.com"
    return None


def _payload(job: Mapping[str, Any]) -> dict[str, Any]:
    description = _text(job.get("job_description"))
    payload: dict[str, Any] = {}
    if _text(job.get("name")):
        payload["title"] = job["name"]
    if description:
        payload["description"] = plain_text(description)
        payload["description_html"] = description
    locations = _locations(job)
    if locations:
        payload["locations"] = locations
    posted = _timestamp(job.get("t_create"))
    if posted:
        payload["posted_at"] = posted
    if _text(job.get("type")):
        payload["employmentType"] = job["type"]
    if _text(job.get("department")):
        payload["department"] = job["department"]
    if _text(job.get("canonicalPositionUrl")):
        payload["url"] = job["canonicalPositionUrl"]
    if _text(job.get("work_location_option")):
        payload["workplaceType"] = job["work_location_option"]
    payload["eightfold"] = dict(job)
    return payload


def _locations(job: Mapping[str, Any]) -> list[str]:
    rows = job.get("locations")
    out: list[str] = []
    if isinstance(rows, list):
        for row in rows:
            text = _text(row)
            if text and text not in out:
                out.append(text)
    if not out:
        text = _text(job.get("location"))
        if text:
            out.append(text)
    return out


def _timestamp(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return _text(value)


def _id_text(value: object) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value))
    return _text(value)


def _job_id(url: str) -> str | None:
    match = _JOB_ID_RE.search(url)
    return match.group(1) if match else None


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
