"""Generic career-site connector (M2).

Used when the classifier cannot recognise an ATS: it fetches the page and
hands the HTML to the extraction pipeline, which is where the actual field
detection happens (``jsonld``, ``embedded_json``, ``static_html``,
``generic``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig


class CareerSiteConnector:
    id = "ats.career_site"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=False,
        get_job=True,
        requires_rendering=False,
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        # This is the fallback connector: it never claims a URL by itself.
        return MatchResult(matched=False, confidence=0.0)

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            yield RawJobRef(source_id=source.id, url=source.url)

        return gen()

    async def fetch_job(self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext) -> Result[RawJob, PortError]:
        response = await ctx.http.send(HttpRequest(method="GET", url=ref.url))
        if not isinstance(response, Ok):
            return response
        html = response.value.body.decode("utf-8", errors="replace")
        return Ok(
            RawJob(
                ref=ref,
                title=None,
                body_text=None,
                body_html=html,
                payload={},
            )
        )
