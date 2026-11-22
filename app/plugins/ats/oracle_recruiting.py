"""Oracle Recruiting Cloud (Fusion HCM) connector (M4+).

Career sites run at ``https://{pod}.fa.{region}.oraclecloud.com/hcmUI/
CandidateExperience/{lang}/sites/{site}/requisitions`` and call two public,
unauthenticated REST resources:

- ``GET .../hcmRestApi/resources/latest/recruitingCEJobRequisitions`` lists a
  site's requisitions. Paging lives inside the ``finder`` parameter:
  ``findReqs;siteNumber={site},limit=100,offset=0,sortBy=POSTING_DATES_DESC``.
  The response envelope is ``items[0]`` with ``TotalJobsCount`` and
  ``requisitionList``.
- ``GET .../recruitingCEJobRequisitionDetails?finder=ById;Id="{id}",
  siteNumber={site}`` returns the full posting, including
  ``ExternalDescriptionStr``, ``ExternalResponsibilitiesStr`` and
  ``ExternalQualificationsStr``.

Some tenants put a WAF in front of the API and answer 403 to non-browser
traffic; the connector reports that as a normal fetch failure.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import urlsplit

from app.domain.models.common import Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.http import HttpRequest
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig
from app.infrastructure.html.parsing import plain_text

API = "/hcmRestApi/resources/latest"
PAGE_SIZE = 100
MAX_PAGES = 5


class OracleRecruitingConnector:
    id = "ats.oracle_recruiting"
    version = "1.0.0"
    capabilities = ConnectorCapabilities(
        list_jobs=True,
        get_job=True,
        supports_pagination=True,
        requires_rendering=False,
        network_hosts=("oraclecloud.com",),
    )

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult:
        host = urlsplit(url if "://" in url else f"https://{url}").netloc.lower()
        lowered = url.lower()
        matched = host.endswith(".oraclecloud.com") and (
            "/hcmui/candidateexperience/" in lowered or "/hcmrestapi/" in lowered
        )
        return MatchResult(
            matched=matched, confidence=0.9 if matched else 0.0, ats_hint="oracle_recruiting"
        )

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]:
        async def gen() -> AsyncIterator[RawJobRef]:
            board = _board(source)
            if board is None:
                raise PortError(
                    code="connector.invalid_source",
                    message="oracle source is not a Candidate Experience site",
                    retryable=False,
                )
            host, site, lang = board
            offset = 0
            for _page in range(MAX_PAGES):
                response = await ctx.http.send(
                    HttpRequest(
                        method="GET",
                        url=(
                            f"https://{host}{API}/recruitingCEJobRequisitions"
                            "?onlyData=true&expand=requisitionList.secondaryLocations"
                            f"&finder=findReqs;siteNumber={site},limit={PAGE_SIZE},"
                            f"offset={offset},sortBy=POSTING_DATES_DESC"
                        ),
                    )
                )
                if not isinstance(response, Ok):
                    raise PortError(
                        code="connector.fetch_failed", message="oracle list failed"
                    )
                data = _json(response.value.body)
                item = _first_item(data)
                jobs = item.get("requisitionList")
                if not isinstance(jobs, list) or not jobs:
                    break
                for job in jobs:
                    if not isinstance(job, Mapping):
                        continue
                    job_id = _text(job.get("Id"))
                    if not job_id:
                        continue
                    yield RawJobRef(
                        source_id=source.id,
                        url=(
                            f"https://{host}/hcmUI/CandidateExperience/{lang}"
                            f"/sites/{site}/job/{job_id}"
                        ),
                        source_posting_id=job_id,
                        updated_at_hint=_text(job.get("PostedDate")),
                    )
                offset += len(jobs)
                total = item.get("TotalJobsCount")
                if len(jobs) < PAGE_SIZE or (isinstance(total, int) and offset >= total):
                    break

        return gen()

    async def fetch_job(
        self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext
    ) -> Result[RawJob, PortError]:
        board = _board(source)
        job_id = ref.source_posting_id
        if board is None or not job_id:
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
        host, site, _lang = board
        response = await ctx.http.send(
            HttpRequest(
                method="GET",
                url=(
                    f"https://{host}{API}/recruitingCEJobRequisitionDetails"
                    f'?finder=ById;Id="{job_id}",siteNumber={site}&onlyData=true'
                ),
            )
        )
        if not isinstance(response, Ok):
            return response
        item = _first_item(_json(response.value.body))
        if not item:
            return Ok(RawJob(ref=ref, title=None, body_text=None, body_html=None, payload={}))
        description_html = _description(item)
        return Ok(
            RawJob(
                ref=ref,
                title=_text(item.get("Title")),
                body_text=plain_text(description_html) if description_html else None,
                body_html=description_html,
                payload=_payload(item),
            )
        )


def _board(source: SourceConfig) -> tuple[str, str, str] | None:
    """Return ``(host, site, lang)`` for an Oracle career site."""
    parts = urlsplit(source.url)
    host = parts.netloc.lower()
    if not host.endswith(".oraclecloud.com"):
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    lowered = [segment.lower() for segment in segments]
    site = ""
    lang = "en"
    if "sites" in lowered:
        index = lowered.index("sites")
        if index + 1 < len(segments):
            site = segments[index + 1]
        if index >= 2 and lowered[index - 2] == "candidateexperience":
            lang = segments[index - 1]
    if not site and source.ats_tenant_key:
        site = source.ats_tenant_key
    if not site:
        site = "CX_1"
    return host, site, lang


def _payload(item: Mapping[str, Any]) -> dict[str, Any]:
    description = _description(item)
    payload: dict[str, Any] = {}
    if _text(item.get("Title")):
        payload["title"] = item["Title"]
    if description:
        payload["description"] = plain_text(description)
        payload["description_html"] = description
    locations = _locations(item)
    if locations:
        payload["locations"] = locations
    posted = _text(item.get("ExternalPostedStartDate")) or _text(item.get("PostedDate"))
    if posted:
        payload["posted_at"] = posted
    if _text(item.get("WorkplaceType")):
        payload["workplaceType"] = item["WorkplaceType"]
    if _text(item.get("JobType")):
        payload["employmentType"] = item["JobType"]
    if _text(item.get("JobSchedule")):
        payload["jobSchedule"] = item["JobSchedule"]
    payload["oracle"] = dict(item)
    return payload


def _description(item: Mapping[str, Any]) -> str | None:
    parts = [
        text
        for text in (
            _text(item.get("ExternalDescriptionStr")),
            _text(item.get("ExternalResponsibilitiesStr")),
            _text(item.get("ExternalQualificationsStr")),
        )
        if text
    ]
    return "\n".join(parts) if parts else None


def _locations(item: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    primary = _text(item.get("PrimaryLocation"))
    if primary:
        out.append(primary)
    secondary = item.get("secondaryLocations")
    if isinstance(secondary, list):
        for row in secondary:
            if not isinstance(row, Mapping):
                continue
            text = _text(row.get("Name")) or _text(row.get("LocationName"))
            if text and text not in out:
                out.append(text)
    return out


def _first_item(data: Mapping[str, Any]) -> dict[str, Any]:
    items = data.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return {}


def _json(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None
