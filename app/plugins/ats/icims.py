"""iCIMS ATS connector (M2).

iCIMS career sites are server-rendered: the search page lists the postings and
each posting page carries schema.org markup. The connector therefore returns
HTML and lets the extraction pipeline (``extract.jsonld`` and friends) do the
field detection.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig
from app.infrastructure.html import parsing

# Link shapes that point at a posting on an iCIMS board.
JOB_PATH_MARKERS = ("/job/", "/jobs/")


class IcmsConnector:
    id = "ats.icims"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        network_hosts=("icims.com",),
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        lowered = url.lower()
        matched = "icims.com" in lowered
        return MatchResult(matched=matched, confidence=0.9 if matched else 0.0, ats_hint="icims")

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            for url in _search_urls(source):
                response = await ctx.http.send(HttpRequest(method="GET", url=url))
                if not isinstance(response, Ok):
                    continue
                html = response.value.body.decode("utf-8", errors="replace")
                found = _job_links(html, url)
                if found:
                    for job_url in found:
                        yield RawJobRef(source_id=source.id, url=job_url)
                    return
            raise PortError(code="connector.fetch_failed", message="icims list failed")

        return gen()

    async def fetch_job(self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext) -> Result[RawJob, PortError]:
        response = await ctx.http.send(HttpRequest(method="GET", url=ref.url))
        if not isinstance(response, Ok):
            return response
        html = response.value.body.decode("utf-8", errors="replace")
        return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=html, payload={}))


def _search_urls(source: SourceConfig) -> list[str]:
    """Candidate search URLs for an iCIMS board."""
    base = source.url.rstrip("/")
    if base.endswith("/jobs"):
        base = base[: -len("/jobs")]
    return [f"{base}/jobs/search?search=&limit=100", f"{base}/jobs?limit=100"]


def _job_links(html: str, page_url: str) -> list[str]:
    """Absolute URLs of the postings linked from a search page."""
    from urllib.parse import urljoin

    urls: list[str] = []
    seen: set[str] = set()
    for link in parsing.links(html):
        href = link.href.strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        if not any(marker in href.lower() for marker in JOB_PATH_MARKERS):
            continue
        absolute = urljoin(page_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        urls.append(absolute)
    return urls
