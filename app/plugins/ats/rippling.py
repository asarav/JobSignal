"""Rippling ATS connector.

Rippling boards live on one host and are addressed by the employer slug:

- ``https://ats.rippling.com/{slug}/jobs`` is the board;
- ``GET https://ats.rippling.com/api/v2/board/{slug}/jobs?page=N`` is the
  unauthenticated listing its own board calls, answering
  ``{items, page, pageSize, totalItems, totalPages}``;
- each item carries ``id``, ``name``, ``url``, ``department`` and
  ``locations``, but no description.

The description lives on the posting page, a Next.js document whose
``__NEXT_DATA__`` payload carries ``apiData.jobPost.description`` as a mapping
of named HTML sections (``role``, ``company``, ...).  One fetch per posting,
parsed from that payload instead of a rendered page.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import urlsplit

from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig
from app.infrastructure.html.parsing import plain_text
from app.plugins.ats.posting_check import invalid_posting, posting_problem
from app.plugins.ats.schema import listing_items

HOST = "ats.rippling.com"
MAX_PAGES = 40
_RESERVED = frozenset({"api", "assets", "static", "www", "app"})
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', re.DOTALL
)


class RipplingConnector:
    id = "ats.rippling"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        supports_pagination=True,
        requires_rendering=False,
        network_hosts=(HOST,),
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        matched = _slug(url) is not None
        return MatchResult(matched=matched, confidence=0.9 if matched else 0.0, ats_hint="rippling")

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            slug = _slug(source.url, source.ats_tenant_key)
            if slug is None:
                raise PortError(
                    code="connector.invalid_source",
                    message="rippling source is not a board slug",
                    retryable=False,
                )
            page = 1
            total_pages = 1
            while page <= min(total_pages, MAX_PAGES):
                url = f"https://{HOST}/api/v2/board/{slug}/jobs?page={page}"
                response = await ctx.http.send(HttpRequest(method="GET", url=url))
                if not isinstance(response, Ok):
                    raise PortError(code="connector.fetch_failed", message="rippling list failed")
                data = _json(response.value.body)
                rows = listing_items(data, "items", ats="rippling", total_key="totalItems")
                for item in rows:
                    if not isinstance(item, Mapping):
                        continue
                    posting_id = _text(item.get("id"))
                    if not posting_id:
                        continue
                    yield RawJobRef(
                        source_id=source.id,
                        url=_text(item.get("url")) or f"https://{HOST}/{slug}/jobs/{posting_id}",
                        source_posting_id=posting_id,
                    )
                reported = data.get("totalPages")
                if not isinstance(reported, int) or reported <= page:
                    break
                total_pages = reported
                page += 1

        return gen()

    async def fetch_job(
        self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext
    ) -> Result[RawJob, PortError]:
        response = await ctx.http.send(HttpRequest(method="GET", url=ref.url))
        if not isinstance(response, Ok):
            return response
        html = response.value.body.decode("utf-8", errors="replace")
        posting = _job_post(html)
        if posting is None:
            problem = posting_problem(html)
            if problem is not None:
                return Err(invalid_posting(self.id, ref.url, problem))
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=html, payload={}))
        description = _description_html(posting)
        return Ok(
            RawJob(
                ref=ref,
                title=_text(posting.get("name")) or _text(posting.get("title")),
                body_text=plain_text(description) if description else None,
                body_html=description or None,
                payload=dict(posting),
            )
        )


def _job_post(html: str) -> Mapping[str, Any] | None:
    """The ``apiData.jobPost`` payload embedded in a Rippling posting page."""
    match = _NEXT_DATA_RE.search(html)
    if match is None:
        return None
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return None
    page_props = data.get("props", {}).get("pageProps", {}) if isinstance(data, dict) else {}
    api_data = page_props.get("apiData") if isinstance(page_props, Mapping) else None
    job = api_data.get("jobPost") if isinstance(api_data, Mapping) else None
    return job if isinstance(job, Mapping) else None


def _description_html(posting: Mapping[str, Any]) -> str:
    """Every named description section, in the order the ATS stored them."""
    sections = posting.get("description")
    if isinstance(sections, Mapping):
        parts = [_text(value) for value in sections.values()]
        return "\n".join(part for part in parts if part)
    if isinstance(sections, str):
        return sections
    return ""


def _slug(url: str, tenant_key: str | None = None) -> str | None:
    """The board slug a Rippling URL names, or ``None`` for the platform."""
    if tenant_key:
        return tenant_key
    parts = urlsplit(url if "://" in url else f"https://{url}")
    if parts.netloc.lower() != HOST:
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments:
        return None
    slug = segments[0].lower()
    return None if slug in _RESERVED else slug


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
