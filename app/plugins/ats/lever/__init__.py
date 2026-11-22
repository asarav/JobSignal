"""Lever ATS connector (M1/M6).

``fetch_job`` reads the posting from Lever's own public posting API.  It used to
return an empty ``RawJob``, which meant any posting the funnel verified from an
index URL was stored as "Untitled" with no description — and the same stub was
the only path a full listing used for its detail fetches.
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


class LeverConnector:
    id = "ats.lever"
    version = "1.1.0"
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
        company = (
            _tenant_from_url(ref.url)
            or source.ats_tenant_key
            or _tenant_from_url(source.url)
        )
        posting_id = ref.source_posting_id or _posting_id_from_url(ref.url)
        if not company or not posting_id:
            raise PortError(
                code="connector.invalid_ref",
                message="lever: posting URL carries no tenant or posting id",
            )
        url = f"https://api.lever.co/v0/postings/{company}/{posting_id}"
        response = await ctx.http.send(HttpRequest(method="GET", url=url))
        if not isinstance(response, Ok):
            raise PortError(code="connector.fetch_failed", message="lever detail failed")
        try:
            data = json.loads(response.value.body.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise PortError(
                code="connector.schema_drift",
                message="lever: detail response was not JSON",
            ) from exc
        if not isinstance(data, dict) or not data.get("text"):
            raise PortError(
                code="connector.not_found",
                message="lever: posting is no longer published",
            )
        body_text, body_html = _body_of(data)
        payload = dict(data)
        payload["title"] = data["text"]
        payload["description"] = body_text
        location = _category(data, "location")
        if location:
            payload["location"] = location
        return Ok(
            RawJob(
                ref=ref,
                title=str(data["text"]),
                body_text=body_text,
                body_html=body_html,
                payload=payload,
            )
        )


def _body_of(data: Mapping[str, Any]) -> tuple[str, str]:
    """Plain text and HTML for a Lever posting, including its section lists."""
    text_parts: list[str] = []
    html_parts: list[str] = []
    plain = str(data.get("descriptionPlain") or "").strip()
    html = str(data.get("description") or "").strip()
    if plain:
        text_parts.append(plain)
    elif html:
        text_parts.append(plain_text(html))
    if html:
        html_parts.append(html)

    lists = data.get("lists")
    if isinstance(lists, list):
        for entry in lists:
            if not isinstance(entry, Mapping):
                continue
            heading = str(entry.get("text") or "").strip()
            content = str(entry.get("content") or "").strip()
            if heading:
                text_parts.append(heading)
                html_parts.append(f"<h3>{heading}</h3>")
            if content:
                text_parts.append(plain_text(content) or content)
                html_parts.append(content)

    additional = str(data.get("additionalPlain") or "").strip()
    if additional:
        text_parts.append(additional)
    elif data.get("additional"):
        text_parts.append(plain_text(str(data["additional"])))
    if data.get("additional"):
        html_parts.append(str(data["additional"]))
    return "\n\n".join(part for part in text_parts if part), "".join(html_parts)


def _category(data: Mapping[str, Any], key: str) -> str:
    categories = data.get("categories")
    if not isinstance(categories, Mapping):
        return ""
    value = categories.get(key)
    return value.strip() if isinstance(value, str) else ""


def _tenant_from_url(url: str) -> str:
    """First path segment of a Lever board URL (``jobs.lever.co/<tenant>``)."""
    segments = [part for part in urlsplit(url).path.split("/") if part]
    return segments[0] if segments else ""


def _posting_id_from_url(url: str) -> str:
    segments = [part for part in urlsplit(url).path.split("/") if part]
    return segments[1] if len(segments) > 1 else ""
