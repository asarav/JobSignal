"""Taleo career-section connector (M4+).

Taleo portals are server-rendered and usually embedded by an employer's
marketing wrapper. Two public pages are enough:

- ``/careersection/{section}/jobsearch.ftl?lang=en`` lists postings as
  ``jobdetail.ftl?job={id}`` links;
- ``/careersection/{section}/jobdetail.ftl?job={id}`` carries the posting.

No Taleo REST endpoint is used; the connector returns detail HTML so the
extraction pipeline can read the schema.org markup or static fields.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import urljoin, urlsplit

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig
from app.infrastructure.html import parsing


class TaleoConnector:
    id = "ats.taleo"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        requires_rendering=False,
        network_hosts=("taleo.net",),
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        host = urlsplit(url if "://" in url else f"https://{url}").netloc.lower()
        matched = host.endswith(".taleo.net") and "/careersection/" in url.lower()
        return MatchResult(matched=matched, confidence=0.9 if matched else 0.0, ats_hint="taleo")

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            for url in _search_urls(source.url):
                response = await ctx.http.send(HttpRequest(method="GET", url=url))
                if not isinstance(response, Ok):
                    continue
                html = response.value.body.decode("utf-8", errors="replace")
                links = _job_links(html, url)
                if links:
                    for link in links:
                        yield RawJobRef(source_id=source.id, url=link)
                    return
            if _is_detail(source.url):
                yield RawJobRef(source_id=source.id, url=source.url)
                return
            raise PortError(code="connector.fetch_failed", message="taleo list failed")

        return gen()

    async def fetch_job(
        self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext
    ) -> Result[RawJob, PortError]:
        response = await ctx.http.send(HttpRequest(method="GET", url=ref.url))
        if not isinstance(response, Ok):
            return response
        html = response.value.body.decode("utf-8", errors="replace")
        return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=html, payload={}))


def _search_urls(url: str) -> list[str]:
    if not _is_detail(url):
        return [url]
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    section = parts.path.split("/careersection/", 1)[-1].split("/", 1)[0]
    if not section:
        return [url]
    return [
        f"{base}/careersection/{section}/jobsearch.ftl?lang=en",
        url,
    ]


def _is_detail(url: str) -> bool:
    return "jobdetail.ftl" in url.lower()


def _job_links(html: str, page_url: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for link in parsing.links(html):
        absolute = urljoin(page_url, link.href.strip())
        lowered = absolute.lower()
        if "jobdetail.ftl" not in lowered or "job=" not in lowered:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        found.append(absolute)
    return found
