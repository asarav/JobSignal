"""Workable ATS connector (M4+).

Workable boards live at ``apply.workable.com/{account}``. The public feed behind
the embeddable careers widget needs no token:

- ``GET https://apply.workable.com/api/v1/widget/accounts/{account}`` returns
  ``{name, description, jobs[]}`` for every published job;
- ``?details=true`` adds each job's HTML description, so the list and the
  detail can share one request (no per-job call).

The feed can carry thousands of rows for large accounts, so the connector
reads one response and yields every job.
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

API_BASE = "https://apply.workable.com/api/v1/widget/accounts"


class WorkableConnector:
    id = "ats.workable"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        supports_pagination=False,
        requires_rendering=False,
        network_hosts=("apply.workable.com", "workable.com"),
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        host = _host(url)
        matched = host == "apply.workable.com" or (
            host.endswith(".workable.com") and host != "www.workable.com"
        )
        return MatchResult(matched=matched, confidence=0.9 if matched else 0.0, ats_hint="workable")

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            account = _account(source)
            if not account:
                raise PortError(
                    code="connector.invalid_source",
                    message="workable source has no account",
                    retryable=False,
                )
            data = await _feed(account, ctx)
            if data is None:
                raise PortError(code="connector.fetch_failed", message="workable list failed")
            for job in _jobs(data):
                shortcode = _text(job.get("shortcode"))
                if not shortcode:
                    continue
                yield RawJobRef(
                    source_id=source.id,
                    url=_text(job.get("url")) or f"https://apply.workable.com/j/{shortcode}",
                    source_posting_id=shortcode,
                    updated_at_hint=_text(job.get("published_on")),
                )

        return gen()

    async def fetch_job(
        self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext
    ) -> Result[RawJob, PortError]:
        account = _account(source)
        if not account:
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
        data = await _feed(account, ctx)
        if data is None:
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
        shortcode = ref.source_posting_id
        job = next(
            (item for item in _jobs(data) if _text(item.get("shortcode")) == shortcode), None
        )
        if job is None:
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
        description = _text(job.get("description"))
        payload = _payload(job, data)
        return Ok(
            RawJob(
                ref=ref,
                title=_text(job.get("title")),
                body_text=plain_text(description) if description else None,
                body_html=description,
                payload=payload,
            )
        )


async def _feed(account: str, ctx: ExecutionContext) -> dict[str, Any] | None:
    response = await ctx.http.send(
        HttpRequest(method="GET", url=f"{API_BASE}/{account}?details=true")
    )
    if not isinstance(response, Ok):
        return None
    try:
        data = json.loads(response.value.body.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _jobs(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    jobs = data.get("jobs")
    return [job for job in jobs if isinstance(job, Mapping)] if isinstance(jobs, list) else []


def _payload(job: Mapping[str, Any], feed: Mapping[str, Any]) -> dict[str, Any]:
    description = _text(job.get("description"))
    locations = _locations(job)
    payload: dict[str, Any] = {}
    if _text(job.get("title")):
        payload["title"] = job["title"]
    if description:
        payload["description"] = plain_text(description)
        payload["description_html"] = description
    if locations:
        payload["locations"] = locations
    if _text(job.get("employment_type")):
        payload["employmentType"] = job["employment_type"]
    if _text(job.get("published_on")):
        payload["posted_at"] = job["published_on"]
    if _text(feed.get("name")):
        payload["companyName"] = feed["name"]
    if _text(job.get("department")):
        payload["department"] = job["department"]
    if job.get("telecommuting") is True:
        payload["remote"] = True
    payload["workable"] = dict(job)
    return payload


def _locations(job: Mapping[str, Any]) -> list[str]:
    rows = job.get("locations")
    out: list[str] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            parts = [
                _text(row.get("city")),
                _text(row.get("region")),
                _text(row.get("country")),
            ]
            text = ", ".join(part for part in parts if part)
            if text and text not in out:
                out.append(text)
    if not out:
        parts = [_text(job.get("city")), _text(job.get("state")), _text(job.get("country"))]
        text = ", ".join(part for part in parts if part)
        if text:
            out.append(text)
    if job.get("telecommuting") is True and "Remote" not in out:
        out.append("Remote")
    return out


def _account(source: SourceConfig) -> str:
    if source.ats_tenant_key:
        return source.ats_tenant_key
    parts = urlsplit(source.url)
    host = parts.netloc.lower()
    if host != "apply.workable.com" and host.endswith(".workable.com"):
        return host[: -len(".workable.com")]
    segments = [segment for segment in parts.path.split("/") if segment]
    return segments[0] if segments else ""


def _host(url: str) -> str:
    return urlsplit(url if "://" in url else f"https://{url}").netloc.lower()


def _text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None
