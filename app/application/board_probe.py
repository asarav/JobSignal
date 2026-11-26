"""Cheap liveness probe for a discovered board, before it is registered.

Discovery proposes career-site URLs from a public web index, and that index is
stale: measured against the shipped candidates, **53%** of settled boards no
longer exist (Workable 87%, Lever 71%, iCIMS 69%, Greenhouse 53%).  Registering
them blindly spends a crawl, a failed run and a slot that a real board could
have used.

This probe asks each ATS the same public question its own board page asks and
answers three things:

* **hiring** — the board exists and lists at least one posting;
* **live_empty** — the board exists and lists none, *when the listing is
  trustworthy* (a JSON count, a server-rendered page).  These boards are kept,
  just at the slowest cadence, because a quiet employer is future supply;
* **dead** — the ATS says it does not know the board (a 404/410, or a redirect
  to the vendor's marketing site).

Anything else (403, 429, 5xx, timeout, a listing rendered client-side where a
zero means "not observed") is **unknown**, and the caller keeps its previous
behaviour rather than treating missing evidence as a negative.

The probe lives in the application layer: it talks through the ``HttpClient``
port and knows no plugin types.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from app.domain.models.common import Ok
from app.domain.models.http import HttpRequest, HttpResponse
from app.domain.ports.http import HttpClient

ProbeState = Literal["hiring", "live_empty", "dead", "unknown"]

#: Connectors whose listing is authoritative about a zero.  iCIMS and Taleo
#: render postings inside an iframe/server shell, so a zero there is "not
#: observed" and must not demote the board.
ZERO_CONFIDENT: frozenset[str] = frozenset(
    {
        "ats.greenhouse",
        "ats.lever",
        "ats.ashby",
        "ats.smartrecruiters",
        "ats.workable",
        "ats.bamboohr",
        "ats.workday",
        "ats.ultipro",
        "ats.oracle_recruiting",
        "ats.paylocity",
        "ats.jazzhr",
        "ats.rippling",
        "ats.personio",
        "ats.breezy",
    }
)

_LOCALE_RE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2,4})?$")
_GREENHOUSE_RE = re.compile(r"^/v1/boards/(?P<tenant>[^/]+)/jobs$")
_WORKDAY_HOST_RE = re.compile(
    r"^(?P<tenant>[a-z0-9][a-z0-9-]*)\.(?:impl-)?wd\d+\.myworkdayjobs\.com$"
)
_WORKDAY_PLAIN_RE = re.compile(r"^(?P<tenant>[a-z0-9][a-z0-9-]*)\.myworkdayjobs\.com$")
_ORACLE_RE = re.compile(
    r"^/hcmUI/CandidateExperience(?:/[a-z]{2}(?:-[A-Za-z]{2,4})?)?"
    r"/sites/(?P<site>[A-Za-z0-9_]+)",
    re.IGNORECASE,
)
_ULTIPRO_RE = re.compile(
    r"^/(?P<tenant>[A-Za-z0-9]{2,32})/JobBoard/"
    r"(?P<board>[0-9a-fA-F-]{36})",
)
_PAYLOCITY_DETAIL_RE = re.compile(r"/recruiting/jobs/details/(\d+)", re.IGNORECASE)
_JAZZHR_DETAIL_RE = re.compile(r"/apply/jobs/details/([A-Za-z0-9]+)")
_TALEO_DETAIL_RE = re.compile(r"jobdetail\.ftl\?job=([A-Za-z0-9_-]+)", re.IGNORECASE)
_ICIMS_JOB_RE = re.compile(r"/jobs/(\d+)/")


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    """The board to probe, identified the way the registry would store it."""

    connector_id: str | None
    url: str
    tenant_key: str | None = None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    state: ProbeState
    postings: int | None = None
    detail: str = ""

    @property
    def live(self) -> bool:
        return self.state in ("hiring", "live_empty")


class BoardProbe:
    def __init__(
        self,
        http: HttpClient,
        *,
        zero_confident: frozenset[str] = ZERO_CONFIDENT,
        timeout_s: float = 15.0,
    ) -> None:
        self._http = http
        self._zero_confident = zero_confident
        self._timeout_s = timeout_s

    async def probe(self, target: ProbeTarget) -> ProbeResult:
        connector = target.connector_id or ""
        handler = {
            "ats.greenhouse": self._greenhouse,
            "ats.lever": self._lever,
            "ats.ashby": self._ashby,
            "ats.smartrecruiters": self._smartrecruiters,
            "ats.workable": self._workable,
            "ats.bamboohr": self._bamboohr,
            "ats.workday": self._workday,
            "ats.ultipro": self._ultipro,
            "ats.oracle_recruiting": self._oracle,
            "ats.paylocity": self._paylocity,
            "ats.jazzhr": self._jazzhr,
            "ats.icims": self._icims,
            "ats.taleo": self._taleo,
            "ats.rippling": self._rippling,
            "ats.personio": self._personio,
            "ats.breezy": self._breezy,
        }.get(connector)
        if handler is None:
            return ProbeResult("unknown", detail="no probe for this connector")
        try:
            return await handler(target)
        except Exception:  # noqa: BLE001 - a probe must never abort a pass
            return ProbeResult("unknown", detail="probe failed")

    # --------------------------------------------------------------- fixed hosts

    async def _greenhouse(self, target: ProbeTarget) -> ProbeResult:
        tenant = _first_segment(target.url)
        if not tenant:
            return ProbeResult("unknown", detail="no tenant")
        return await self._json_count(
            f"https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs",
            lambda data: len(data.get("jobs", [])) if isinstance(data, dict) else None,
        )

    async def _lever(self, target: ProbeTarget) -> ProbeResult:
        tenant = _first_segment(target.url)
        if not tenant:
            return ProbeResult("unknown", detail="no tenant")
        return await self._json_count(
            f"https://api.lever.co/v0/postings/{tenant}?mode=json",
            lambda data: len(data) if isinstance(data, list) else None,
        )

    async def _ashby(self, target: ProbeTarget) -> ProbeResult:
        tenant = _first_segment(target.url)
        if not tenant:
            return ProbeResult("unknown", detail="no tenant")
        return await self._json_count(
            f"https://api.ashbyhq.com/posting-api/job-board/{tenant}",
            lambda data: (
                len(data.get("jobs", []))
                if isinstance(data, dict) and isinstance(data.get("jobs"), list)
                else None
            ),
        )

    async def _smartrecruiters(self, target: ProbeTarget) -> ProbeResult:
        tenant = _first_segment(target.url)
        if not tenant:
            return ProbeResult("unknown", detail="no tenant")
        return await self._json_count(
            f"https://api.smartrecruiters.com/v1/companies/{tenant}/postings?limit=1",
            lambda data: (
                int(data.get("totalFound", 0)) if isinstance(data, dict) else None
            ),
        )

    async def _workable(self, target: ProbeTarget) -> ProbeResult:
        tenant = _first_segment(target.url)
        if not tenant:
            return ProbeResult("unknown", detail="no tenant")
        return await self._json_count(
            f"https://apply.workable.com/api/v1/widget/accounts/{tenant}",
            lambda data: len(data.get("jobs", [])) if isinstance(data, dict) else None,
        )

    # --------------------------------------------------------------- subdomains

    async def _bamboohr(self, target: ProbeTarget) -> ProbeResult:
        host = _host(target.url)
        response = await self._get(f"https://{host}/careers/list")
        if response is None:
            return ProbeResult("unknown", detail="no answer")
        if response.status in (404, 410):
            return ProbeResult("dead", detail="board gone")
        if response.status != 200:
            return ProbeResult("unknown", detail=f"http {response.status}")
        if _host(response.url) != host:
            # An unknown tenant redirects to BambooHR's marketing site with 200.
            return ProbeResult("dead", detail="redirected off the tenant host")
        data = _json(response.body)
        if not isinstance(data, dict):
            return ProbeResult("unknown", detail="listing is not JSON")
        rows = data.get("result")
        if not isinstance(rows, list):
            return ProbeResult("unknown", detail="listing has no result")
        return self._from_count(len(rows), "ats.bamboohr")

    async def _workday(self, target: ProbeTarget) -> ProbeResult:
        host = _host(target.url)
        tenant = (target.tenant_key or "").split("|")[0] or _workday_tenant(host)
        site = _workday_site(target.url)
        if not tenant or not site:
            return ProbeResult("unknown", detail="no tenant/site")
        response = await self._post(
            f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
            {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
        )
        if response is None:
            return ProbeResult("unknown", detail="no answer")
        if response.status in (404, 410):
            return ProbeResult("dead", detail="board gone")
        if response.status != 200:
            return ProbeResult("unknown", detail=f"http {response.status}")
        data = _json(response.body)
        if not isinstance(data, dict):
            return ProbeResult("unknown", detail="listing is not JSON")
        total = data.get("total")
        if isinstance(total, int):
            return self._from_count(total, "ats.workday")
        postings = data.get("jobPostings")
        if isinstance(postings, list):
            return self._from_count(len(postings), "ats.workday")
        return ProbeResult("unknown", detail="listing has no count")

    async def _ultipro(self, target: ProbeTarget) -> ProbeResult:
        match = _ULTIPRO_RE.match(urlsplit(target.url).path)
        if match is None:
            return ProbeResult("unknown", detail="no board id")
        url = (
            f"https://{_host(target.url)}/{match.group('tenant')}/JobBoard/"
            f"{match.group('board')}/JobBoardView/LoadSearchResults"
        )
        response = await self._post(
            url,
            {
                "opportunitySearch": {
                    "Top": 1,
                    "Skip": 0,
                    "QueryString": "",
                    "OrderBy": [
                        {"Value": "date", "PropertyName": "PostedDate", "Ascending": False}
                    ],
                    "Filters": [],
                }
            },
        )
        if response is None:
            return ProbeResult("unknown", detail="no answer")
        if response.status in (404, 410):
            return ProbeResult("dead", detail="board gone")
        if response.status != 200:
            return ProbeResult("unknown", detail=f"http {response.status}")
        data = _json(response.body)
        if not isinstance(data, dict):
            return ProbeResult("unknown", detail="listing is not JSON")
        total = data.get("totalCount")
        if isinstance(total, int):
            return self._from_count(total, "ats.ultipro")
        rows = data.get("opportunities")
        if isinstance(rows, list):
            return self._from_count(len(rows), "ats.ultipro")
        return ProbeResult("unknown", detail="listing has no count")

    async def _oracle(self, target: ProbeTarget) -> ProbeResult:
        host = _host(target.url)
        site = ""
        key = (target.tenant_key or "").strip()
        if "|" in key:
            host = key.split("|")[0]
            site = key.split("|")[1]
        else:
            match = _ORACLE_RE.search(urlsplit(target.url).path)
            site = match.group("site") if match else ""
        if not site:
            return ProbeResult("unknown", detail="no site")
        url = (
            f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            f"?onlyData=true&finder=findReqs;siteNumber={site},limit=1,offset=0"
        )
        response = await self._get(url)
        if response is None:
            return ProbeResult("unknown", detail="no answer")
        if response.status in (404, 410):
            return ProbeResult("dead", detail="board gone")
        if response.status != 200:
            return ProbeResult("unknown", detail=f"http {response.status}")
        data = _json(response.body)
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            return ProbeResult("unknown", detail="finder returned nothing")
        head = items[0]
        total = head.get("TotalJobsCount")
        if isinstance(total, int):
            return self._from_count(total, "ats.oracle_recruiting")
        rows = head.get("requisitionList")
        if isinstance(rows, list):
            return self._from_count(len(rows), "ats.oracle_recruiting")
        return ProbeResult("unknown", detail="finder has no count")

    async def _rippling(self, target: ProbeTarget) -> ProbeResult:
        slug = target.tenant_key or _first_segment(target.url)
        if not slug:
            return ProbeResult("unknown", detail="no slug")
        return await self._json_count(
            f"https://ats.rippling.com/api/v2/board/{slug}/jobs",
            lambda data: (
                len(data.get("items", []))
                if isinstance(data, dict) and isinstance(data.get("items"), list)
                else None
            ),
        )

    # ------------------------------------------------------ server-rendered pages

    async def _personio(self, target: ProbeTarget) -> ProbeResult:
        host = _host(target.url)
        response = await self._get(f"https://{host}/xml")
        if response is None:
            return ProbeResult("unknown", detail="no answer")
        if response.status in (404, 410):
            return ProbeResult("dead", detail="board gone")
        if response.status != 200:
            return ProbeResult("unknown", detail=f"http {response.status}")
        if _host(response.url) != host:
            return ProbeResult("dead", detail="redirected off the tenant host")
        body = response.body
        if b"workzag-jobs" not in body:
            # A 200 that is not the feed is an error page, not an empty board.
            return ProbeResult("unknown", detail="listing is not the XML feed")
        return self._from_count(body.count(b"<position>"), "ats.personio")

    async def _breezy(self, target: ProbeTarget) -> ProbeResult:
        host = _host(target.url)
        response = await self._get(f"https://{host}/json")
        if response is None:
            return ProbeResult("unknown", detail="no answer")
        if response.status in (404, 410):
            return ProbeResult("dead", detail="board gone")
        if response.status != 200:
            return ProbeResult("unknown", detail=f"http {response.status}")
        if _host(response.url) != host:
            return ProbeResult("dead", detail="redirected off the tenant host")
        data = _json(response.body)
        if not isinstance(data, list):
            return ProbeResult("unknown", detail="listing shape changed")
        return self._from_count(len(data), "ats.breezy")

    async def _paylocity(self, target: ProbeTarget) -> ProbeResult:
        response = await self._get(target.url)
        if response is None:
            return ProbeResult("unknown", detail="no answer")
        if response.status in (404, 410):
            return ProbeResult("dead", detail="board gone")
        if response.status != 200:
            return ProbeResult("unknown", detail=f"http {response.status}")
        company = _paylocity_company(target.url)
        if company and company not in response.url.lower():
            return ProbeResult("dead", detail="redirected off the company")
        text = response.body.decode("utf-8", errors="replace")
        return self._from_count(len(set(_PAYLOCITY_DETAIL_RE.findall(text))), "ats.paylocity")

    async def _jazzhr(self, target: ProbeTarget) -> ProbeResult:
        host = _host(target.url)
        response = await self._get(f"https://{host}/apply/jobs")
        if response is None:
            return ProbeResult("unknown", detail="no answer")
        if response.status in (404, 410):
            return ProbeResult("dead", detail="board gone")
        if response.status != 200:
            return ProbeResult("unknown", detail=f"http {response.status}")
        if _host(response.url) != host:
            # JazzHR answers an unknown tenant with its own marketing site.
            return ProbeResult("dead", detail="redirected off the tenant host")
        text = response.body.decode("utf-8", errors="replace")
        return self._from_count(len(set(_JAZZHR_DETAIL_RE.findall(text))), "ats.jazzhr")

    async def _icims(self, target: ProbeTarget) -> ProbeResult:
        host = _host(target.url)
        response = await self._get(f"{target.url.rstrip('/')}/jobs/search?ss=1&in_iframe=1")
        if response is None:
            return ProbeResult("unknown", detail="no answer")
        if response.status in (404, 410):
            return ProbeResult("dead", detail="board gone")
        if response.status != 200:
            return ProbeResult("unknown", detail=f"http {response.status}")
        if _host(response.url) != host:
            return ProbeResult("dead", detail="redirected off the tenant host")
        text = response.body.decode("utf-8", errors="replace")
        return self._from_count(len(set(_ICIMS_JOB_RE.findall(text))), "ats.icims")

    async def _taleo(self, target: ProbeTarget) -> ProbeResult:
        host = _host(target.url)
        response = await self._get(target.url)
        if response is None:
            return ProbeResult("unknown", detail="no answer")
        if response.status in (404, 410):
            return ProbeResult("dead", detail="board gone")
        if response.status != 200:
            return ProbeResult("unknown", detail=f"http {response.status}")
        if _host(response.url) != host:
            return ProbeResult("dead", detail="redirected off the tenant host")
        text = response.body.decode("utf-8", errors="replace")
        return self._from_count(len(set(_TALEO_DETAIL_RE.findall(text))), "ats.taleo")

    # ------------------------------------------------------------------ helpers

    def _from_count(self, count: int, connector_id: str) -> ProbeResult:
        if count > 0:
            return ProbeResult("hiring", count)
        if connector_id in self._zero_confident:
            return ProbeResult("live_empty", 0)
        # A zero we cannot trust is not evidence of an empty board.
        return ProbeResult("unknown", None, detail="zero not observed")

    async def _json_count(
        self, url: str, count: Callable[[object], int | None]
    ) -> ProbeResult:
        response = await self._get(url)
        if response is None:
            return ProbeResult("unknown", detail="no answer")
        if response.status in (404, 410):
            return ProbeResult("dead", detail="board gone")
        if response.status != 200:
            return ProbeResult("unknown", detail=f"http {response.status}")
        postings = count(_json(response.body))
        if not isinstance(postings, int):
            return ProbeResult("unknown", detail="listing shape changed")
        return self._from_count(postings, _connector_of(url))

    async def _get(self, url: str) -> HttpResponse | None:
        result = await self._http.send(
            HttpRequest(method="GET", url=url, timeout_s=self._timeout_s)
        )
        return result.value if isinstance(result, Ok) else None

    async def _post(self, url: str, body: dict[str, object]) -> HttpResponse | None:
        result = await self._http.send(
            HttpRequest(
                method="POST",
                url=url,
                json_body=body,
                headers={"content-type": "application/json"},
                timeout_s=self._timeout_s,
            )
        )
        return result.value if isinstance(result, Ok) else None


def _host(url: str) -> str:
    return urlsplit(url if "://" in url else f"https://{url}").netloc.lower()


def _first_segment(url: str) -> str:
    segments = [part for part in urlsplit(url).path.split("/") if part]
    return segments[0] if segments else ""


def _workday_tenant(host: str) -> str:
    match = _WORKDAY_HOST_RE.match(host) or _WORKDAY_PLAIN_RE.match(host)
    return match.group("tenant") if match else ""


def _workday_site(url: str) -> str:
    segments = [part for part in urlsplit(url).path.split("/") if part]
    if segments and _LOCALE_RE.match(segments[0]):
        segments = segments[1:]
    return segments[0] if segments else ""


def _paylocity_company(url: str) -> str:
    match = re.search(
        r"/recruiting/jobs/all/([0-9a-f-]{36})", urlsplit(url).path, re.IGNORECASE
    )
    return match.group(1).lower() if match else ""


def _connector_of(url: str) -> str:
    """Which connector a probe URL belongs to (for the zero-confidence rule)."""
    host = _host(url)
    if "greenhouse" in host:
        return "ats.greenhouse"
    if host == "api.lever.co":
        return "ats.lever"
    if "ashbyhq" in host:
        return "ats.ashby"
    if "smartrecruiters" in host:
        return "ats.smartrecruiters"
    if "workable" in host:
        return "ats.workable"
    if host == "ats.rippling.com":
        return "ats.rippling"
    return ""


def _json(body: bytes) -> object:
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return None
