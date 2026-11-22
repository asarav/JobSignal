"""Oracle Recruiting Cloud discovery provider.

An ORC career site lives on a tenant host under an Oracle HCM pod, e.g.
``https://cbcz.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX``.
The tenant is a *subdomain* of a pod and the board also needs the ``sites``
code, so neither a fixed-host prefix query nor certificate transparency (the
pods use wildcard certificates) enumerates it.

A domain query scoped to one pod does work — measured at 17–38 s — and returns
full URLs, from which the board is reconstructed. The whole ``oraclecloud.com``
domain is too large for the archive gateway (504), so this provider rotates a
list of HCM pods instead: two pods per pass, wrapping after the list ends.
A throttled pod is logged and revisited on the next wrap; nothing is raised.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from app.domain.models.common import Evidence, Ok
from app.domain.models.context import ExecutionContext
from app.domain.models.discovery import DiscoveredSource, DiscoveryQuery, ProviderCapabilities
from app.domain.models.http import HttpRequest

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"

#: Oracle Fusion HCM pods seen in public career URLs. New regions are added as
#: they appear; a pod that no longer answers is simply skipped each pass.
PODS: tuple[str, ...] = (
    "fa.us2.oraclecloud.com",
    "fa.us6.oraclecloud.com",
    "fa.em2.oraclecloud.com",
    "fa.uk1.oraclecloud.com",
    "fa.ap1.oraclecloud.com",
    "fa.ap2.oraclecloud.com",
    "fa.ap5.oraclecloud.com",
    "fa.ca1.oraclecloud.com",
    "fa.sa1.oraclecloud.com",
    "fa.au1.oraclecloud.com",
    "fa.jp1.oraclecloud.com",
    "fa.in1.oraclecloud.com",
    "fa.eu1.oraclecloud.com",
    "fa.eu2.oraclecloud.com",
    "fa.us1.oraclecloud.com",
    "fa.us4.oraclecloud.com",
    "fa.us5.oraclecloud.com",
    "fa.us7.oraclecloud.com",
    "fa.us8.oraclecloud.com",
    "fa.us9.oraclecloud.com",
    "fa.us10.oraclecloud.com",
    "fa.uk2.oraclecloud.com",
    "fa.ca2.oraclecloud.com",
    "fa.mx1.oraclecloud.com",
    "fa.br1.oraclecloud.com",
    "fa.ch1.oraclecloud.com",
    "fa.de1.oraclecloud.com",
    "fa.fr1.oraclecloud.com",
    "fa.it1.oraclecloud.com",
    "fa.nl1.oraclecloud.com",
    "fa.es1.oraclecloud.com",
    "fa.se1.oraclecloud.com",
    "fa.ae1.oraclecloud.com",
    "fa.il1.oraclecloud.com",
    "fa.za1.oraclecloud.com",
)

#: Pods per pass. A pod query is the expensive part (17–38 s measured).
PODS_PER_PASS = 2

#: Rows requested per pod. Each row is a full career-site URL; a few hundred
#: cover a pod's indexed tenants without asking for a huge response.
CDX_LIMIT = 500

#: One attempt: the pass should not spend 85 s twice on a dead pod.
TIMEOUT_S = 85.0

_ORC_PATH_RE = re.compile(
    r"^/hcmUI/CandidateExperience"
    r"(?:/(?P<lang>[a-z]{2}(?:-[A-Za-z]{2,4})?))?"
    r"/sites/(?P<site>[A-Za-z0-9_]+)(?:/|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class OracleBoard:
    url: str
    host: str
    site: str
    company_hint: str | None

    @property
    def key(self) -> str:
        return f"{self.host}/{self.site}"


class OracleRecruitingProvider:
    """Enumerates ORC Candidate Experience sites from public index rows."""

    id = "discovery.oracle_recruiting"
    version = "1.0.0"
    capabilities = ProviderCapabilities(
        offline=False,
        network_hosts=("web.archive.org",),
        requires_opt_in=False,
    )

    def __init__(
        self,
        pods: Sequence[str] = PODS,
        *,
        cdx_limit: int = CDX_LIMIT,
        pods_per_pass: int = PODS_PER_PASS,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        self._pods = tuple(pods) or PODS
        self._cdx_limit = max(1, cdx_limit)
        self._pods_per_pass = max(1, pods_per_pass)
        self._timeout_s = timeout_s

    @property
    def rotation_size(self) -> int:
        return len(self._pods)

    def pods_for(self, cursor: int, slices: int) -> list[str]:
        if not self._pods:
            return []
        pass_index = max(0, cursor) // max(1, slices)
        start = pass_index * self._pods_per_pass
        return [
            self._pods[(start + offset) % len(self._pods)]
            for offset in range(self._pods_per_pass)
        ]

    async def discover(
        self, query: DiscoveryQuery, ctx: ExecutionContext
    ) -> AsyncIterator[DiscoveredSource]:
        raw_cursor = query.extra.get("cursor", 0)
        cursor = raw_cursor if isinstance(raw_cursor, int) else 0
        raw_slices = query.extra.get("slices", 1)
        slices = raw_slices if isinstance(raw_slices, int) and raw_slices > 0 else 1
        pods = self.pods_for(cursor, slices)

        async def fetch(pod: str) -> list[OracleBoard]:
            rows = await self._rows(pod, ctx)
            if not rows:
                return []
            boards: dict[str, OracleBoard] = {}
            for url in rows:
                board = orc_board(url)
                if board is not None:
                    boards.setdefault(board.key, board)
            return list(boards.values())

        outcomes = await asyncio.gather(*(fetch(pod) for pod in pods), return_exceptions=True)
        observed_at = datetime.now(UTC)
        seen: set[str] = set()
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                ctx.logger.warning(
                    "discovery.oracle_recruiting.slice_failed", error=str(outcome)
                )
                continue
            for board in outcome:
                if board.key in seen:
                    continue
                seen.add(board.key)
                yield DiscoveredSource(
                    kind="ats",
                    url=board.url,
                    company_hint=board.company_hint,
                    ats_hint="ats.oracle_recruiting",
                    confidence=0.65,
                    evidence=[
                        Evidence(
                            kind="oracle_pod_index",
                            description="Candidate Experience site is listed in the public index",
                            data={"pod": board.host, "site": board.site},
                            observed_at=observed_at,
                        )
                    ],
                    tenant_key=board.site,
                )

    async def _rows(self, pod: str, ctx: ExecutionContext) -> list[str] | None:
        url = cdx_url(pod, self._cdx_limit)
        response = await ctx.http.send(
            HttpRequest(method="GET", url=url, timeout_s=self._timeout_s)
        )
        if isinstance(response, Ok):
            return _urls(response.value.body)
        ctx.logger.warning(
            "discovery.oracle_recruiting.cdx_failed",
            pod=pod,
            error=response.error.message,
        )
        return None


def cdx_url(pod: str, limit: int = CDX_LIMIT) -> str:
    """The index query for one pod (exported so tests can key fixtures)."""
    return (
        f"{CDX_ENDPOINT}?url={pod}&matchType=domain"
        "&filter=original:.*/hcmUI/CandidateExperience/.*"
        f"&fl=original&collapse=urlkey&limit={limit}&output=json"
    )


def orc_board(url: str) -> OracleBoard | None:
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if not host.endswith(".oraclecloud.com") or ".fa." not in host or "*" in host:
        return None
    match = _ORC_PATH_RE.match(parts.path)
    if match is None:
        return None
    site = match.group("site")
    lang = match.group("lang") or "en"
    tenant = host.split(".fa.", 1)[0]
    if tenant in {"admin", "idcs", "login", "www", "api"}:
        return None
    return OracleBoard(
        url=(
            f"https://{host}/hcmUI/CandidateExperience/{lang}/sites/{site}"
        ),
        host=host,
        site=site,
        company_hint=tenant.replace("-", " ").title() if tenant else None,
    )


def _urls(body: bytes) -> list[str]:
    try:
        table = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return []
    if not isinstance(table, list):
        return []
    return [
        row[0] for row in table[1:] if isinstance(row, list) and row and isinstance(row[0], str)
    ]
