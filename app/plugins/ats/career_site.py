"""Generic career-site connector (M2+).

Two jobs, in order:

1. If the page embeds a known ATS (Workday, Taleo, Oracle, Eightfold, Phenom,
   iCIMS, ...), route listing and detail requests to that connector. Marketing
   wrappers (Beamery, Radancy, Clinch, SmashFly) usually expose the real ATS in
   an iframe or apply URL, so one generic path covers hundreds of them.
2. Otherwise harvest job-like links from the page (and its sitemap when the
   page has few links) and hand each detail page to the extraction pipeline.
   Pages with no public listings stay single-URL sources rather than being
   scraped through a private endpoint.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import replace
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig
from app.domain.services.classifier import classify_url
from app.domain.services.wrappers import EmbeddedAts, detect_embedded_ats
from app.infrastructure.html import parsing

#: Detail paths seen across marketing sites, Apple, Google and Meta.
_JOB_PATH_RE = re.compile(
    r"/(?:jobs?|careers?|positions?|openings?|vacanc(?:y|ies)|details?|"
    r"opportunit(?:y|ies))/[^/?#]+",
    re.IGNORECASE,
)
_JOB_QUERY_MARKERS = (
    "jobid=",
    "job_id=",
    "gh_jid=",
    "requisition=",
    "req_id=",
    "posting=",
)
_NOT_JOB_SEGMENTS = frozenset(
    {
        "search",
        "results",
        "login",
        "signin",
        "privacy",
        "terms",
        "home",
        "teams",
        "locations",
        "life-at",
        "benefits",
        "students",
        "events",
        "how-we-hire",
        "dashboard",
        "recommendations",
        "saved",
        "applications",
        "jobsearch",
        "search-results",
    }
)
_MAX_PAGE_LINKS = 200
_MAX_SITEMAP_LINKS = 200
_MAX_CHILD_SITEMAPS = 3
_MIN_LINKS_BEFORE_SITEMAP = 3
_SITEMAP_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)


class CareerSiteConnector:
    id = "ats.career_site"
    version = "1.1.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        requires_rendering=False,
    )

    def __init__(self) -> None:
        #: Wrapper detection is stable for a source URL; cache it so a listing
        #: with many detail pages does not refetch the wrapper page per job.
        self._embedded_cache: dict[str, EmbeddedAts | None] = {}

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        # This is the fallback connector: it never claims a URL by itself.
        return MatchResult(matched=False, confidence=0.0)

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            html, page_url = await _fetch(source.url, ctx)
            embedded = detect_embedded_ats(html, page_url)
            self._embedded_cache[source.url] = embedded
            if embedded is not None and embedded.connector_id != self.id:
                refs = await _delegate_list(source, embedded, ctx)
                if refs:
                    for ref in refs:
                        yield replace(ref, source_id=source.id)
                    return
            urls = _harvest_links(html, page_url)
            if len(urls) < _MIN_LINKS_BEFORE_SITEMAP:
                urls = _dedupe(urls + await _sitemap_links(page_url, ctx))
            if not urls:
                yield RawJobRef(source_id=source.id, url=page_url)
                return
            for url in urls[:_MAX_PAGE_LINKS]:
                yield RawJobRef(source_id=source.id, url=url)

        return gen()

    async def fetch_job(
        self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext
    ) -> Result[RawJob, PortError]:
        embedded = self._embedded_cache.get(source.url)
        if embedded is None and source.url not in self._embedded_cache:
            html, page_url = await _fetch(source.url, ctx)
            embedded = detect_embedded_ats(html, page_url)
            self._embedded_cache[source.url] = embedded
        if embedded is not None and embedded.connector_id != self.id:
            result = await _delegate_fetch(source, embedded, ref, ctx)
            if result is not None:
                return result
        direct = classify_url(ref.url)[0]
        if direct and direct != self.id:
            result = await _fetch_with_connector(direct, ref, ctx)
            if result is not None:
                return result
        response = await ctx.http.send(HttpRequest(method="GET", url=ref.url))
        if not isinstance(response, Ok):
            return response
        body = response.value.body.decode("utf-8", errors="replace")
        return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=body, payload={}))


async def _fetch(url: str, ctx: ExecutionContext) -> tuple[str, str]:
    response = await ctx.http.send(HttpRequest(method="GET", url=url))
    if not isinstance(response, Ok):
        raise PortError(code="connector.fetch_failed", message="career site fetch failed")
    html = response.value.body.decode("utf-8", errors="replace")
    return html, response.value.url or url


def _connector_for(connector_id: str):
    from app.plugins.ats.amazon import AmazonConnector
    from app.plugins.ats.eightfold import EightfoldConnector
    from app.plugins.ats.phenom import PhenomConnector
    from app.plugins.ats.taleo import TaleoConnector

    factories = {
        "ats.amazon": AmazonConnector,
        "ats.eightfold": EightfoldConnector,
        "ats.phenom": PhenomConnector,
        "ats.taleo": TaleoConnector,
    }
    factory = factories.get(connector_id)
    if factory is not None:
        return factory()
    from app.plugins.ats.icims import IcmsConnector
    from app.plugins.ats.oracle_recruiting import OracleRecruitingConnector
    from app.plugins.ats.smartrecruiters import SmartRecruitersConnector
    from app.plugins.ats.workable import WorkableConnector
    from app.plugins.ats.workday import WorkdayConnector

    fallbacks = {
        "ats.workday": WorkdayConnector,
        "ats.icims": IcmsConnector,
        "ats.oracle_recruiting": OracleRecruitingConnector,
        "ats.workable": WorkableConnector,
        "ats.smartrecruiters": SmartRecruitersConnector,
    }
    factory = fallbacks.get(connector_id)
    return factory() if factory is not None else None


async def _delegate_list(
    source: SourceConfig, embedded, ctx: ExecutionContext
) -> list[RawJobRef]:
    connector = _connector_for(embedded.connector_id)
    if connector is None:
        return []
    delegated = replace(
        source,
        url=embedded.url,
        connector_id=embedded.connector_id,
        ats_tenant_key=embedded.tenant_key,
    )
    try:
        return [ref async for ref in await connector.list_jobs(delegated, ctx)]
    except Exception:  # noqa: BLE001 - fall back to link harvesting
        return []


async def _delegate_fetch(
    source: SourceConfig, embedded, ref: RawJobRef, ctx: ExecutionContext
) -> Result[RawJob, PortError] | None:
    connector = _connector_for(embedded.connector_id)
    if connector is None:
        return None
    delegated = replace(
        source,
        url=embedded.url,
        connector_id=embedded.connector_id,
        ats_tenant_key=embedded.tenant_key,
    )
    try:
        return await connector.fetch_job(ref, delegated, ctx)
    except Exception:  # noqa: BLE001 - fall back to the direct page
        return None


async def _fetch_with_connector(
    connector_id: str, ref: RawJobRef, ctx: ExecutionContext
) -> Result[RawJob, PortError] | None:
    connector = _connector_for(connector_id)
    if connector is None:
        return None
    source = SourceConfig(
        id=ref.source_id,
        url=ref.url,
        kind="ats",
        connector_id=connector_id,
    )
    try:
        return await connector.fetch_job(ref, source, ctx)
    except Exception:  # noqa: BLE001 - fall back to the direct page
        return None


def _harvest_links(html: str, page_url: str) -> list[str]:
    found: list[str] = []
    page_host = urlsplit(page_url).netloc.lower()
    for link in parsing.links(html):
        href = link.href.strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        if href.startswith("jobs/results/") and "google.com/about/careers" in page_url:
            absolute = urljoin(
                "https://www.google.com/about/careers/applications/", href
            ).split("#", 1)[0]
        else:
            absolute = urljoin(page_url, href).split("#", 1)[0]
        parts = urlsplit(absolute)
        if parts.scheme not in {"http", "https"}:
            continue
        if parts.netloc.lower() != page_host:
            continue
        path = parts.path.lower()
        if path.endswith(_ASSET_SUFFIXES) or "/assets/" in path:
            continue
        query = f"?{parts.query.lower()}"
        if page_host.endswith("google.com") and not _is_google_job(parts, page_host):
            continue
        job_path = _JOB_PATH_RE.search(path)
        last = path.rstrip("/").rsplit("/", 1)[-1]
        if _is_google_job(parts, page_host) and not last[:1].isdigit():
            continue
        if job_path and last not in _NOT_JOB_SEGMENTS:
            found.append(_normalise_link(absolute))
            continue
        if any(marker in query for marker in _JOB_QUERY_MARKERS):
            found.append(_normalise_link(absolute))
    return _dedupe(found)


async def _sitemap_links(page_url: str, ctx: ExecutionContext) -> list[str]:
    parts = urlsplit(page_url)
    root = f"{parts.scheme}://{parts.netloc}"
    urls: list[str] = []
    child_maps: list[str] = []
    for path in ("/sitemap.xml", "/sitemap_index.xml"):
        response = await ctx.http.send(HttpRequest(method="GET", url=f"{root}{path}"))
        if not isinstance(response, Ok):
            continue
        text = response.value.body.decode("utf-8", errors="replace")
        for loc in _SITEMAP_LOC_RE.findall(text):
            absolute = urljoin(root, loc)
            if absolute.lower().endswith(".xml"):
                child_maps.append(absolute)
            else:
                urls.append(absolute)
        if urls or child_maps:
            break
    for child in child_maps[:_MAX_CHILD_SITEMAPS]:
        response = await ctx.http.send(HttpRequest(method="GET", url=child))
        if not isinstance(response, Ok):
            continue
        text = response.value.body.decode("utf-8", errors="replace")
        urls.extend(urljoin(root, loc) for loc in _SITEMAP_LOC_RE.findall(text))
    found: list[str] = []
    for url in urls:
        path = urlsplit(url).path.lower()
        last = path.rstrip("/").rsplit("/", 1)[-1]
        if _JOB_PATH_RE.search(path) and last not in _NOT_JOB_SEGMENTS:
            found.append(_normalise_link(url))
        if len(found) >= _MAX_SITEMAP_LINKS:
            break
    return _dedupe(found)


_ASSET_SUFFIXES = (
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".pdf",
    ".woff",
    ".woff2",
)


def _is_google_job(parts, page_host: str) -> bool:
    """Google's public results page mixes job cards with site navigation."""
    return page_host.endswith("google.com") and "/about/careers/applications/jobs/results/" in parts.path


def _normalise_link(url: str) -> str:
    """Drop duplicate Apple login variants and keep the canonical job URL."""
    parts = urlsplit(url)
    path = parts.path
    if path.lower().endswith("/locationpicker"):
        path = path[: -len("/locationPicker")]
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _dedupe(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        parts = urlsplit(url)
        path = parts.path.rstrip("/")
        key = f"{parts.netloc.lower()}{path}"
        # A path-only listing can carry the posting id in its query.
        if not _JOB_PATH_RE.search(path.lower()):
            key = f"{key}?{parts.query}"
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out
