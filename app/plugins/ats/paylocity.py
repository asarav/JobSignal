"""Paylocity connector (M4+).

A Paylocity career site lives on one shared host and is addressed by the
employer's company id:

- ``https://recruiting.paylocity.com/Recruiting/Jobs/All/{company_id}`` lists
  the openings as ``/Recruiting/Jobs/Details/{posting_id}`` links;
- ``/Recruiting/Jobs/Details/{posting_id}`` carries the posting.

The company id **is** the board. The readable slug after it is decorative and
one employer is indexed under several slugs (measured: ``LayerCake`` and
``SKYNET-INNOVATIONS`` for the same id), so nothing here may key a board on it.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig
from app.plugins.ats.posting_check import invalid_posting, posting_problem

HOST = "recruiting.paylocity.com"
BOARD_PREFIX = "/recruiting/jobs/all/"
DETAIL_PATH = "/Recruiting/Jobs/Details/"
_DETAIL_RE = re.compile(r"/recruiting/jobs/details/(\d+)", re.IGNORECASE)
_GUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_COMPANY_RE = re.compile(rf"^/recruiting/jobs/all/(?P<company>{_GUID})(?:/|$)", re.IGNORECASE)
_GUID_RE = re.compile(rf"^{_GUID}$", re.IGNORECASE)


class PaylocityConnector:
    id = "ats.paylocity"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        supports_pagination=False,
        requires_rendering=False,
        network_hosts=(HOST,),
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        matched = company_id(url) is not None
        return MatchResult(
            matched=matched, confidence=0.9 if matched else 0.0, ats_hint="paylocity"
        )

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            company = _company(source)
            board = f"https://{HOST}/Recruiting/Jobs/All/{company}" if company else None
            if board is None:
                raise PortError(
                    code="connector.invalid_source",
                    message="paylocity source has no company id",
                    retryable=False,
                )
            response = await ctx.http.send(HttpRequest(method="GET", url=board))
            if not isinstance(response, Ok):
                raise PortError(code="connector.fetch_failed", message="paylocity list failed")
            html = response.value.body.decode("utf-8", errors="replace")
            for posting_id in dict.fromkeys(_DETAIL_RE.findall(html)):
                yield RawJobRef(
                    source_id=source.id,
                    url=f"https://{HOST}{DETAIL_PATH}{posting_id}",
                    source_posting_id=posting_id,
                )

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


def company_id(url: str) -> str | None:
    """The company id a Paylocity URL names, lower-cased."""
    if "://" not in url:
        url = f"https://{HOST}/{url.lstrip('/')}"
    match = _COMPANY_RE.match(urlsplit(url).path.lower())
    return match.group("company") if match else None


def _company(source: SourceConfig) -> str | None:
    """The board's company id, from the tenant key or the source URL."""
    key = (source.ats_tenant_key or "").strip().lower()
    if key and _GUID_RE.match(key):
        return key
    return company_id(source.url)


def board_url(url: str) -> str | None:
    """The canonical board URL for a Paylocity URL, without its slug."""
    company = company_id(url)
    return f"https://{HOST}/Recruiting/Jobs/All/{company}" if company else None
