"""UKG Pro Recruiting (formerly UltiPro) connector (M4+).

Public boards live on UKG's recruiting hosts at
``{host}/{tenant}/JobBoard/{board_id}`` (``recruiting.ultipro.com``, the
``recruiting2``/``recruiting3`` shards and ``recruiting.ultipro.ca``). The board
is a JavaScript app backed by two unauthenticated endpoints:

- ``POST {base}/JobBoardView/LoadSearchResults`` lists postings, paginated with
  ``Top``/``Skip`` (50 per page) and reporting ``totalCount``;
- ``GET  {base}/OpportunityDetail?opportunityId={id}`` renders one posting, with
  the full record embedded as
  ``new US.Opportunity.CandidateOpportunityDetail({...})``.

The detail payload is JSON inside a script block, so it is read with a
string-aware brace-balancing parser: a regex breaks on braces and quotes inside
job descriptions.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import urlsplit

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig
from app.infrastructure.html.parsing import plain_text

#: Hosts that serve UKG Pro boards. The shard and the Canadian host are
#: separate names, so the connector accepts each one the board itself uses.
HOSTS = (
    "recruiting.ultipro.com",
    "recruiting2.ultipro.com",
    "recruiting3.ultipro.com",
    "recruiting.ultipro.ca",
)

#: Rows per listing request; the board's own UI asks for 50.
PAGE_SIZE = 50

#: Safety cap: 20 pages = 1,000 postings per crawl. Bounded like the other
#: connectors so one enormous board cannot monopolise a discovery pass.
MAX_PAGES = 20

_BOARD_PATH_RE = re.compile(
    r"^/(?P<tenant>[A-Za-z0-9]{2,32})/JobBoard/"
    r"(?P<board>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:/|$)"
)
_DETAIL_MARKER = "new US.Opportunity.CandidateOpportunityDetail("


def _search_body(skip: int) -> dict[str, object]:
    return {
        "opportunitySearch": {
            "Top": PAGE_SIZE,
            "Skip": skip,
            "QueryString": "",
            "OrderBy": [{"Value": "date", "PropertyName": "PostedDate", "Ascending": False}],
            "Filters": [],
        }
    }


class UltiproConnector:
    id = "ats.ultipro"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        supports_pagination=True,
        requires_rendering=False,
        network_hosts=HOSTS,
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        lowered = url.lower()
        host = urlsplit(lowered if "://" in lowered else f"https://{lowered}").netloc
        matched = host in HOSTS and "/jobboard/" in lowered
        return MatchResult(matched=matched, confidence=0.9 if matched else 0.0, ats_hint="ultipro")

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            board = _board(source)
            if board is None:
                raise PortError(
                    code="connector.invalid_source",
                    message="ultipro source is not a JobBoard URL",
                    retryable=False,
                )
            base = board.base_url
            skip = 0
            seen: set[str] = set()
            for _page in range(MAX_PAGES):
                response = await ctx.http.send(
                    HttpRequest(
                        method="POST",
                        url=f"{base}/JobBoardView/LoadSearchResults",
                        json_body=_search_body(skip),
                        headers={"content-type": "application/json"},
                    )
                )
                if not isinstance(response, Ok):
                    raise PortError(
                        code="connector.fetch_failed", message="ultipro list failed"
                    )
                data = _json(response.value.body)
                opportunities = data.get("opportunities")
                if not isinstance(opportunities, list) or not opportunities:
                    break
                for opportunity in opportunities:
                    if not isinstance(opportunity, dict):
                        continue
                    posting_id = opportunity.get("Id")
                    if posting_id is None:
                        continue
                    posting_id = str(posting_id)
                    if posting_id in seen:
                        continue
                    seen.add(posting_id)
                    yield RawJobRef(
                        source_id=source.id,
                        url=f"{base}/OpportunityDetail?opportunityId={posting_id}",
                        source_posting_id=posting_id,
                        updated_at_hint=_as_text(opportunity.get("PostedDate")),
                    )
                skip += len(opportunities)
                total = _as_int(data.get("totalCount"))
                if len(opportunities) < PAGE_SIZE or (total is not None and skip >= total):
                    break

        return gen()

    async def fetch_job(
        self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext
    ) -> Result[RawJob, PortError]:
        url = ref.url
        if not url.startswith("http"):
            board = _board(source)
            if board is None or ref.source_posting_id is None:
                return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
            url = f"{board.base_url}/OpportunityDetail?opportunityId={ref.source_posting_id}"
        response = await ctx.http.send(HttpRequest(method="GET", url=url))
        if not isinstance(response, Ok):
            return response
        html = response.value.body.decode("utf-8", errors="replace")
        data = _embedded_opportunity(html)
        if data is None:
            # The board changed its inline script: hand the page to the
            # extraction pipeline instead of dropping the posting.
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=html, payload={}))
        description = data.get("Description")
        description_html = description if isinstance(description, str) else None
        return Ok(
            RawJob(
                ref=ref,
                title=_as_text(data.get("Title")),
                body_text=plain_text(description_html) if description_html else None,
                body_html=description_html,
                payload=_payload(data),
            )
        )


class _Board:
    __slots__ = ("host", "tenant", "board_id")

    def __init__(self, host: str, tenant: str, board_id: str) -> None:
        self.host = host
        self.tenant = tenant
        self.board_id = board_id

    @property
    def base_url(self) -> str:
        return f"https://{self.host}/{self.tenant}/JobBoard/{self.board_id}"


def _board(source: SourceConfig) -> _Board | None:
    """Parse the host, tenant and board id out of a UKG Pro source."""
    parts = urlsplit(source.url)
    host = parts.netloc.lower()
    if host not in HOSTS:
        return None
    match = _BOARD_PATH_RE.match(parts.path)
    if match:
        return _Board(host, match.group("tenant").upper(), match.group("board").lower())
    # A bare board URL (``.../{tenant}``) still works when the source stores
    # the board id from the seed file.
    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments or source.ats_tenant_key is None:
        return None
    return _Board(host, segments[0].upper(), source.ats_tenant_key)


def _payload(data: Mapping[str, Any]) -> dict[str, Any]:
    """The detail record, plus lowercase keys the extraction pipeline reads."""
    locations = [
        text
        for text in (_location_text(item) for item in _as_list(data.get("Locations")))
        if text
    ]
    description = data.get("Description")
    description_text = plain_text(description) if isinstance(description, str) else None
    payload: dict[str, Any] = {}
    if data.get("Title"):
        payload["title"] = data["Title"]
    if description_text:
        payload["description"] = description_text
    if description:
        payload["description_html"] = description
    if locations:
        payload["locations"] = locations
    if data.get("PostedDate"):
        payload["posted_at"] = data["PostedDate"]
    full_time = data.get("FullTime")
    if isinstance(full_time, bool):
        payload["employmentType"] = "FULL_TIME" if full_time else "PART_TIME"
    if data.get("RequisitionNumber") is not None:
        payload["requisitionNumber"] = data["RequisitionNumber"]
    if data.get("JobCategoryName") is not None:
        payload["jobCategory"] = data["JobCategoryName"]
    if data.get("HoursPerWeek") is not None:
        payload["hoursPerWeek"] = data["HoursPerWeek"]
    # Keep the vendor record for provenance; it is small and already parsed.
    payload["ultipro"] = dict(data)
    return payload


def _location_text(location: Any) -> str | None:
    if not isinstance(location, Mapping):
        return None
    name = _as_text(location.get("LocalizedName"))
    address = location.get("Address")
    if not isinstance(address, Mapping):
        return name
    city = _as_text(address.get("City"))
    region = address.get("State")
    state = _as_text(region.get("Code")) if isinstance(region, Mapping) else None
    country = address.get("Country")
    country_name = _as_text(country.get("Name")) if isinstance(country, Mapping) else None
    parts = [part for part in (name, city, state) if part]
    if parts and state and city and name:
        # ``Acme - Ankeny, IA`` reads better than duplicate names.
        parts = [city, state]
    if not parts and country_name:
        parts = [country_name]
    if not parts:
        return None
    text = ", ".join(parts)
    if country_name and country_name not in ("United States", "USA") and name is None:
        text = f"{text}, {country_name}"
    return text


def _embedded_opportunity(html: str) -> dict[str, Any] | None:
    """Parse ``new US.Opportunity.CandidateOpportunityDetail({...})``."""
    start = html.find(_DETAIL_MARKER)
    if start < 0:
        return None
    body = html[start + len(_DETAIL_MARKER):]
    payload = _balanced_json(body)
    if payload is None:
        return None
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _balanced_json(text: str) -> str | None:
    """The first complete JSON object in ``text``, string and escape aware."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _json(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
