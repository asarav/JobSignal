"""Taleo career-section connector (M4+).

Taleo portals are server-rendered and usually embedded by an employer's
marketing wrapper. A section serves:

- ``/careersection/{section}/jobsearch.ftl?lang=en``, whose *listing* is
  rendered client-side: the page names the section's portal id and its own
  script posts the query to
  ``/careersection/rest/jobboard/searchjobs?lang=en&portal={portal}``, which
  answers JSON (``requisitionList`` and ``pagingData.totalCount``);
- ``/careersection/{section}/jobdetail.ftl?job={id}``, which carries the
  posting.

Older sections still put ``jobdetail.ftl?job=`` links in the search page's
HTML, so those links remain the fallback listing.

No Taleo REST endpoint is used beyond the one the page itself calls; the
connector returns detail HTML so the extraction pipeline can read the
schema.org markup or static fields.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from urllib.parse import urljoin, urlsplit

from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig
from app.infrastructure.html import parsing
from app.plugins.ats.posting_check import invalid_posting, posting_problem

#: The section's portal id, which the faceted-search endpoint requires.
_PORTAL_RE = re.compile(r"portal=(\d+)")
_SEARCH_PATH = "/careersection/rest/jobboard/searchjobs?lang=en&portal={portal}"
#: The endpoint answers 25 rows a page; a section with more than this many pages
#: (2,000 postings) is capped rather than walked forever.
_PAGE_SIZE = 25
_MAX_PAGES = 80


class TaleoConnector:
    id = "ats.taleo"
    version = "1.1.0"
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
                portal = _PORTAL_RE.search(html)
                if portal is not None:
                    listed = await _section_refs(source, ctx, url, portal.group(1))
                    if listed:
                        for ref in listed:
                            yield ref
                        return
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
        problem = posting_problem(html)
        if problem is not None:
            return Err(invalid_posting(self.id, ref.url, problem))
        return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=html, payload={}))


async def _section_refs(
    source: SourceConfig, ctx: ExecutionContext, page_url: str, portal: str
) -> list[RawJobRef]:
    """Every posting the section lists, through its own search endpoint.

    The endpoint answers an empty frame when the caller's time zone is missing,
    so the headers matter; an unreachable or refused endpoint returns ``[]`` and
    the caller falls back to the page's links.
    """
    parts = urlsplit(page_url)
    host = parts.netloc.lower()
    section = parts.path.split("/careersection/", 1)[-1].split("/", 1)[0]
    if not section:
        return []
    endpoint = f"{parts.scheme or 'https'}://{host}{_SEARCH_PATH.format(portal=portal)}"
    found: list[RawJobRef] = []
    seen: set[str] = set()
    for page in range(1, _MAX_PAGES + 1):
        response = await ctx.http.send(
            HttpRequest(
                method="POST",
                url=endpoint,
                headers={
                    "content-type": "application/json",
                    "tz": "UTC",
                    "tzname": "UTC",
                    "referer": page_url,
                },
                json_body={"fieldData": {}, "multilineEnabled": False, "pageNo": page},
            )
        )
        if not isinstance(response, Ok):
            break
        try:
            data = json.loads(response.value.body.decode("utf-8", errors="replace"))
        except ValueError:
            break
        if not isinstance(data, dict) or data.get("careerSectionUnAvailable"):
            break
        rows = data.get("requisitionList")
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            job = str(row.get("jobId") or "") if isinstance(row, dict) else ""
            if not job or job in seen:
                continue
            seen.add(job)
            found.append(
                RawJobRef(
                    source_id=source.id,
                    url=f"https://{host}/careersection/{section}/jobdetail.ftl?job={job}&lang=en",
                )
            )
        if len(rows) < _PAGE_SIZE:
            break
        paging = data.get("pagingData")
        total = paging.get("totalCount") if isinstance(paging, dict) else None
        if isinstance(total, int) and len(found) >= total:
            break
    return found


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
