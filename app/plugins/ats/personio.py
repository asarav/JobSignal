"""Personio ATS connector.

A Personio board is a tenant subdomain of one of two career-site domains
(``{tenant}.jobs.personio.de`` and ``{tenant}.jobs.personio.com``) and publishes
a single XML feed, the same one its own careers page reads:

- ``GET https://{tenant}.jobs.personio.de/xml`` returns every published
  position: ``id``, ``name``, ``office``, ``department``, ``employmentType``,
  ``createdAt`` and its HTML sections under ``jobDescriptions``;
- ``/job/{id}`` is the public posting page.

The feed carries the descriptions, so a posting is assembled from it rather
than from one rendered page per job.  The XML is parsed with the standard
library's ``xml.etree`` (bytes in, so the declared encoding is honoured).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
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

#: Career-site domains.  A tenant lives on exactly one of them; both are
#: enumerated because the split is not visible from the tenant label.
DOMAINS = ("jobs.personio.de", "jobs.personio.com")
_RESERVED = frozenset({"www", "api", "app", "static", "assets", "cdn"})


class PersonioConnector:
    id = "ats.personio"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        supports_pagination=False,
        requires_rendering=False,
        network_hosts=("jobs.personio.de", "jobs.personio.com"),
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        matched = _board_host(url) is not None
        return MatchResult(matched=matched, confidence=0.9 if matched else 0.0, ats_hint="personio")

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            host = _host_for_source(source)
            if host is None:
                raise PortError(
                    code="connector.invalid_source",
                    message="personio source is not a careers subdomain",
                    retryable=False,
                )
            positions = await _positions(host, ctx)
            if positions is None:
                raise PortError(code="connector.fetch_failed", message="personio list failed")
            for position in positions:
                posting_id = _text(position.get("id"))
                if not posting_id:
                    continue
                yield RawJobRef(
                    source_id=source.id,
                    url=f"https://{host}/job/{posting_id}",
                    source_posting_id=posting_id,
                    updated_at_hint=_text(position.get("createdAt")),
                )

        return gen()

    async def fetch_job(
        self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext
    ) -> Result[RawJob, PortError]:
        host = _host_for_source(source) or _board_host(ref.url)
        posting_id = ref.source_posting_id
        if host is None or not posting_id:
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
        positions = await _positions(host, ctx)
        if positions is None:
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
        position = next(
            (item for item in positions if _text(item.get("id")) == posting_id), None
        )
        if position is None:
            # The position is gone from the feed; the page itself decides
            # whether that is a retired posting or a transient failure.
            response = await ctx.http.send(HttpRequest(method="GET", url=ref.url))
            if not isinstance(response, Ok):
                return response
            html = response.value.body.decode("utf-8", errors="replace")
            return Err(invalid_posting(self.id, ref.url, posting_problem(html) or "position gone"))
        description = _description_html(position)
        return Ok(
            RawJob(
                ref=ref,
                title=_text(position.get("name")),
                body_text=plain_text(description) if description else None,
                body_html=description or None,
                payload=position,
            )
        )


async def _positions(host: str, ctx: ExecutionContext) -> list[dict[str, Any]] | None:
    """Every position in a board's XML feed, or ``None`` when it did not answer."""
    response = await ctx.http.send(HttpRequest(method="GET", url=f"https://{host}/xml"))
    if not isinstance(response, Ok):
        return None
    return parse_positions(response.value.body)


def parse_positions(body: bytes) -> list[dict[str, Any]]:
    """Positions from a Personio XML feed.

    Raises ``connector.schema_drift`` rather than returning an empty list when
    the document is not a feed at all: a renamed root element must fail the
    crawl loudly instead of looking like a board with no openings.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise PortError(
            code="connector.schema_drift",
            message=f"personio: XML feed is not parseable ({exc})",
        ) from exc
    if root.tag != "workzag-jobs":
        raise PortError(
            code="connector.schema_drift",
            message=f"personio: unexpected feed root <{root.tag}>",
        )
    return [_position(element) for element in root.findall("position")]


def _position(element: ET.Element) -> dict[str, Any]:
    position: dict[str, Any] = {}
    for child in element:
        if child.tag == "jobDescriptions":
            continue
        if len(child):
            position[child.tag] = [_section(grandchild) for grandchild in child]
            continue
        text = _text(child.text)
        if text is not None:
            position[child.tag] = text
    descriptions = [
        section.findtext("value")
        for section in element.findall("jobDescriptions/jobDescription")
    ]
    position["jobDescriptions"] = [text for text in descriptions if text]
    return position


def _section(element: ET.Element) -> dict[str, Any]:
    """One nested element (a description section) as name/value pairs."""
    return {
        "name": _text(element.findtext("name")),
        "value": element.findtext("value"),
    }


def _description_html(position: Mapping[str, Any]) -> str:
    sections = position.get("jobDescriptions")
    if not isinstance(sections, list):
        return ""
    return "\n".join(str(section) for section in sections if section)


def _host_for_source(source: SourceConfig) -> str | None:
    if source.ats_tenant_key:
        tenant = source.ats_tenant_key.strip().lower()
        domain = _domain_for(source.url)
        return f"{tenant}.{domain}" if domain else None
    return _board_host(source.url)


def _board_host(url: str) -> str | None:
    """The tenant host of a Personio URL, or ``None`` for the platform."""
    host = urlsplit(url if "://" in url else f"https://{url}").netloc.lower()
    for domain in DOMAINS:
        suffix = f".{domain}"
        if not host.endswith(suffix):
            continue
        label = host[: -len(suffix)]
        if not label or "." in label or label in _RESERVED:
            return None
        return host
    return None


def _domain_for(url: str) -> str | None:
    host = urlsplit(url if "://" in url else f"https://{url}").netloc.lower()
    for domain in DOMAINS:
        if host.endswith(f".{domain}"):
            return domain
    return None


def _text(value: str | None) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None
