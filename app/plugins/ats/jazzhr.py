"""JazzHR connector (M4+).

JazzHR boards are server-rendered on the employer's own subdomain:

- ``https://{tenant}.applytojob.com/apply/jobs`` lists the openings as
  ``/apply/jobs/details/{code}`` links;
- ``/apply/jobs/details/{code}`` carries one posting.

An unknown tenant does not answer 404: the request is redirected to
``www.jazzhr.com`` (measured), so a board is only live while the answer stays on
the tenant's own host.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig

HOST_SUFFIX = ".applytojob.com"
LIST_PATH = "/apply/jobs"
DETAIL_PATH = "/apply/jobs/details/"
_DETAIL_RE = re.compile(r"/apply/jobs/details/([A-Za-z0-9]+)")
_TENANT_RE = re.compile(r"^(?P<tenant>[a-z0-9][a-z0-9-]*)$")


class JazzHrConnector:
    id = "ats.jazzhr"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        supports_pagination=False,
        requires_rendering=False,
        network_hosts=("applytojob.com",),
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        matched = _board_host(url) is not None
        return MatchResult(matched=matched, confidence=0.9 if matched else 0.0, ats_hint="jazzhr")

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            host = _board_host(source.url)
            if host is None:
                raise PortError(
                    code="connector.invalid_source",
                    message="jazzhr source is not a board subdomain",
                    retryable=False,
                )
            list_url = f"https://{host}{LIST_PATH}"
            response = await ctx.http.send(HttpRequest(method="GET", url=list_url))
            if not isinstance(response, Ok):
                raise PortError(code="connector.fetch_failed", message="jazzhr list failed")
            landed = urlsplit(response.value.url or list_url).netloc.lower()
            if landed != host:
                # The tenant does not exist and JazzHR answered with its own
                # marketing site, which lists no board postings.
                raise PortError(
                    code="connector.invalid_source",
                    message="jazzhr board is not on its tenant host",
                    retryable=False,
                )
            html = response.value.body.decode("utf-8", errors="replace")
            for code in dict.fromkeys(_DETAIL_RE.findall(html)):
                yield RawJobRef(
                    source_id=source.id,
                    url=f"https://{host}{DETAIL_PATH}{code}",
                    source_posting_id=code,
                )

        return gen()

    async def fetch_job(
        self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext
    ) -> Result[RawJob, PortError]:
        response = await ctx.http.send(HttpRequest(method="GET", url=ref.url))
        if not isinstance(response, Ok):
            return response
        html = response.value.body.decode("utf-8", errors="replace")
        return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=html, payload={}))


def _board_host(url: str) -> str | None:
    """The tenant host of a JazzHR URL, or ``None`` for the platform itself."""
    host = urlsplit(url if "://" in url else f"https://{url}").netloc.lower()
    if not host.endswith(HOST_SUFFIX):
        return None
    tenant = host[: -len(HOST_SUFFIX)]
    if not _TENANT_RE.match(tenant):
        return None
    return host
