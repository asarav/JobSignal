"""Amazon Jobs connector (M4+).

Amazon publishes a public search document used by amazon.jobs itself:

``GET https://www.amazon.jobs/en/search.json?base_query=&loc_query=&result_limit=100&offset=0``

The response carries ``jobs`` (with ``id_icims``, ``title``, ``job_path``,
locations, posted date, description and qualifications) and ``hits`` for
paging. Detail pages are server-rendered HTML, so the connector returns the
page and lets the extraction strategies read the posting.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import urlencode, urlsplit

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig
from app.infrastructure.html import parsing
from app.plugins.ats.schema import listing_items

PAGE_SIZE = 100
MAX_PAGES = 5
SEARCH_PATH = "/en/search.json"


class AmazonConnector:
    id = "ats.amazon"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        supports_pagination=True,
        requires_rendering=False,
        network_hosts=("amazon.jobs",),
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        host = _host(url)
        matched = host == "amazon.jobs" or host == "www.amazon.jobs"
        return MatchResult(matched=matched, confidence=0.9 if matched else 0.0, ats_hint="amazon")

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            offset = 0
            for _page in range(MAX_PAGES):
                params = urlencode(
                    {
                        "base_query": "",
                        "loc_query": "",
                        "result_limit": PAGE_SIZE,
                        "offset": offset,
                    }
                )
                response = await ctx.http.send(
                    HttpRequest(method="GET", url=f"https://www.amazon.jobs{SEARCH_PATH}?{params}")
                )
                if not isinstance(response, Ok):
                    raise PortError(
                        code="connector.fetch_failed", message="amazon list failed"
                    )
                data = _json(response.value.body)
                jobs = listing_items(
                    data, "jobs", ats="amazon", total_key="hits", first_page=offset == 0
                )
                if not jobs:
                    break
                for job in jobs:
                    if not isinstance(job, Mapping):
                        continue
                    job_id = _id_text(job.get("id_icims")) or _id_text(job.get("id"))
                    path = _text(job.get("job_path"))
                    if not job_id or not path:
                        continue
                    yield RawJobRef(
                        source_id=source.id,
                        url=f"https://www.amazon.jobs{path}",
                        source_posting_id=job_id,
                        updated_at_hint=_text(job.get("posted_date")),
                    )
                offset += len(jobs)
                total = data.get("hits")
                if len(jobs) < PAGE_SIZE or (
                    isinstance(total, int) and offset >= total
                ):
                    break

        return gen()

    async def fetch_job(
        self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext
    ) -> Result[RawJob, PortError]:
        response = await ctx.http.send(HttpRequest(method="GET", url=ref.url))
        if not isinstance(response, Ok):
            return response
        html = response.value.body.decode("utf-8", errors="replace")
        title = next(
            (heading.text for heading in parsing.headings(html) if heading.level == 1),
            None,
        )
        return Ok(
            RawJob(
                ref=ref,
                title=title,
                body_text=None,
                body_html=html,
                payload={"url": ref.url},
            )
        )


def _host(url: str) -> str:
    return urlsplit(url if "://" in url else f"https://{url}").netloc.lower()


def _json(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _id_text(value: object) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value))
    return _text(value)


def _text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None
