"""Domain-index discovery provider: ATS hosts the fixed-host rotation cannot reach.

``discovery.web_index`` asks the public URL index for ``host/{leading
character}``, which only works when the employer is the *first path segment*.
Several common ATSs put the employer somewhere else:

- Workday: ``{tenant}.wd{N}.myworkdayjobs.com/{locale}/{site}`` (subdomain, and
  the board URL also needs the site that follows the locale);
- iCIMS: ``{tenant}.icims.com`` (subdomain);
- BambooHR: ``{tenant}.bamboohr.com`` (subdomain);
- Eightfold: ``{tenant}.eightfold.ai`` (subdomain; the API domain is resolved
  from the board page, so the tenant label is stored as the key);
- UKG Pro: ``recruiting.ultipro.com/{tenant}/JobBoard/{board_id}`` (two path
  segments);
- Workable: ``apply.workable.com/{account}`` (fixed host, but the board needs a
  prefix scan rather than the general fixed-host parsing).

This provider walks those targets with a *domain* index query and a leading
character filter (``url=icims.com&matchType=domain&filter=original:^https?://a``)
and reconstructs a valid board URL from each captured URL — including the
Workday locale and site, which the hostname alone does not carry.

Domain scans are heavier than prefix scans at the archive gateway (tens of
seconds each), so a pass walks only a couple of slices.  The cursor still
advances with the pass, so coverage grows every day and wraps, and no bundled
seed is required for a new tenant to be found.  Failures return nothing rather
than raising: a throttled archive must not hold the other providers' cursor.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode, urlsplit

from app.domain.models.common import Evidence, Ok
from app.domain.models.context import ExecutionContext
from app.domain.models.discovery import DiscoveredSource, DiscoveryQuery, ProviderCapabilities
from app.domain.models.http import HttpRequest

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"

#: One leading character of the tenant/host label per slice.
BUCKETS = "abcdefghijklmnopqrstuvwxyz"

#: Domain scans answer in 40–90 s each; a few concurrent slices keep a pass
#: inside its 300 s shim timeout while still advancing coverage every day.
SLICES_PER_PASS = 3

#: Rows requested per slice. Enough for a bucket's tenants, small enough that
#: the gateway answers in a reasonable time.
CDX_LIMIT = 1000

#: Concurrent slice requests stay low: bursts are what the gateway throttles.
SLICE_CONCURRENCY = 3

_WORKDAY_HOST_RE = re.compile(
    r"^(?P<tenant>[a-z0-9][a-z0-9-]*)\.(?:impl-)?wd\d+\.myworkdayjobs\.com$"
)
_WORKDAY_PLAIN_RE = re.compile(r"^(?P<tenant>[a-z0-9][a-z0-9-]*)\.myworkdayjobs\.com$")
_ICIMS_HOST_RE = re.compile(r"^(?P<tenant>[a-z0-9][a-z0-9-]*)\.icims\.com$")
_BAMBOO_HOST_RE = re.compile(r"^(?P<tenant>[a-z0-9][a-z0-9-]*)\.bamboohr\.com$")
_EIGHTFOLD_HOST_RE = re.compile(r"^(?P<tenant>[a-z0-9][a-z0-9-]*)\.eightfold\.ai$")
_LOCALE_RE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2,4})?$")
_ULTIPRO_HOSTS = frozenset(
    {
        "recruiting.ultipro.com",
        "recruiting2.ultipro.com",
        "recruiting3.ultipro.com",
        "recruiting.ultipro.ca",
    }
)
_ULTIPRO_PATH_RE = re.compile(
    r"^/(?P<tenant>[A-Za-z0-9]{2,32})/JobBoard/"
    r"(?P<board>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:/|$)"
)
_RESERVED_LABELS = frozenset(
    {"www", "www1", "www2", "assets", "api", "app", "cdn", "static", "login", "secure"}
)
_RESERVED_WORKABLE = frozenset({"api", "j", "jobs", "app", "assets", "www"})
_RESERVED_WORKDAY_SITES = frozenset({"wday", "cxs", "assets", "static"})


@dataclass(frozen=True, slots=True)
class IndexTarget:
    """One indexed ATS host and how the employer is addressed on it."""

    target: str
    connector_id: str
    match: str  # "domain" for subdomains, "prefix" for a fixed host

    @property
    def slice_count(self) -> int:
        return len(BUCKETS)


DEFAULT_TARGETS: tuple[IndexTarget, ...] = (
    IndexTarget("myworkdayjobs.com", "ats.workday", "domain"),
    IndexTarget("icims.com", "ats.icims", "domain"),
    IndexTarget("bamboohr.com", "ats.bamboohr", "domain"),
    IndexTarget("eightfold.ai", "ats.eightfold", "domain"),
    IndexTarget("recruiting.ultipro.com", "ats.ultipro", "prefix"),
    IndexTarget("apply.workable.com", "ats.workable", "prefix"),
)


@dataclass(frozen=True, slots=True)
class Candidate:
    url: str
    tenant: str
    company_hint: str | None
    connector_id: str
    tenant_key: str | None = None
    #: Boards reachable through more than one URL shape (Workday with and
    #: without a locale prefix) collapse on this key.
    dedupe_key: str | None = None

    @property
    def key(self) -> str:
        return self.dedupe_key or self.url


class DomainIndexProvider:
    """Enumerates subdomain/path ATS tenants from a public URL index."""

    id = "discovery.domain_index"
    version = "1.0.0"
    capabilities = ProviderCapabilities(
        offline=False,
        network_hosts=("web.archive.org",),
        requires_opt_in=False,
    )

    def __init__(
        self,
        targets: Sequence[IndexTarget] = DEFAULT_TARGETS,
        *,
        cdx_limit: int = CDX_LIMIT,
        slices_per_pass: int = SLICES_PER_PASS,
        timeout_s: float = 85.0,
    ) -> None:
        self._targets = tuple(targets) or DEFAULT_TARGETS
        self._cdx_limit = max(1, cdx_limit)
        self._slices_per_pass = max(1, slices_per_pass)
        self._timeout_s = timeout_s

    @property
    def rotation_size(self) -> int:
        return len(self._targets) * len(BUCKETS)

    def slices_for(self, cursor: int, slices: int) -> list[tuple[IndexTarget, str]]:
        """The ``(target, bucket)`` pairs this pass walks.

        The pass cursor advances by ``slices`` every run, so dividing it by the
        step gives a daily pass number; each pass takes the next
        ``slices_per_pass`` pairs and wraps at the end of the rotation.
        """
        flat = [(target, bucket) for target in self._targets for bucket in BUCKETS]
        if not flat:
            return []
        step = max(1, slices)
        pass_index = max(0, cursor) // step
        start = pass_index * self._slices_per_pass
        return [flat[(start + offset) % len(flat)] for offset in range(self._slices_per_pass)]

    async def discover(
        self, query: DiscoveryQuery, ctx: ExecutionContext
    ) -> AsyncIterator[DiscoveredSource]:
        raw_cursor = query.extra.get("cursor", 0)
        cursor = raw_cursor if isinstance(raw_cursor, int) else 0
        raw_slices = query.extra.get("slices", 1)
        slices = raw_slices if isinstance(raw_slices, int) and raw_slices > 0 else 1
        pairs = self.slices_for(cursor, slices)
        semaphore = asyncio.Semaphore(SLICE_CONCURRENCY)

        async def fetch(pair: tuple[IndexTarget, str]) -> list[Candidate]:
            async with semaphore:
                return await self._slice(pair[0], pair[1], ctx)

        outcomes = await asyncio.gather(*(fetch(pair) for pair in pairs), return_exceptions=True)
        observed_at = datetime.now(UTC)
        seen: set[str] = set()
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                ctx.logger.warning("discovery.domain_index.slice_failed", error=str(outcome))
                continue
            for candidate in outcome:
                if candidate.url in seen:
                    continue
                seen.add(candidate.url)
                yield DiscoveredSource(
                    kind="ats",
                    url=candidate.url,
                    company_hint=candidate.company_hint,
                    ats_hint=candidate.connector_id,
                    confidence=0.6,
                    evidence=[
                        Evidence(
                            kind="domain_index",
                            description="tenant is listed in the public web index",
                            data={"captured_url": candidate.url},
                            observed_at=observed_at,
                        )
                    ],
                    tenant_key=candidate.tenant_key,
                )

    async def _slice(
        self, target: IndexTarget, bucket: str, ctx: ExecutionContext
    ) -> list[Candidate]:
        rows = await self._rows(target, bucket, ctx)
        if rows is None:
            return []
        # Workday commonly indexes both ``/site`` and ``/en-US/site``; keep
        # the longer (locale-bearing) form and drop the duplicate.
        found: dict[str, Candidate] = {}
        for url in rows:
            candidate = _candidate(target.connector_id, url)
            if candidate is None:
                continue
            current = found.get(candidate.key)
            if current is None or len(candidate.url) > len(current.url):
                found[candidate.key] = candidate
        return list(found.values())

    async def _rows(
        self, target: IndexTarget, bucket: str, ctx: ExecutionContext
    ) -> list[str] | None:
        url = _cdx_url(target, bucket, self._cdx_limit)
        # One attempt only: domain scans already take tens of seconds, and a
        # retry would push a pass past the shim timeout. A failed slice is
        # simply re-walked when the rotation comes round again.
        started = time.monotonic()
        response = await ctx.http.send(
            HttpRequest(method="GET", url=url, timeout_s=self._timeout_s)
        )
        if isinstance(response, Ok):
            rows = _urls(response.value.body)
            ctx.logger.info(
                "discovery.domain_index.slice",
                target=target.target,
                bucket=bucket,
                bytes=len(response.value.body),
                rows=0 if rows is None else len(rows),
                parsed=rows is not None,
                elapsed_s=round(time.monotonic() - started, 1),
            )
            if rows is None:
                ctx.logger.warning(
                    "discovery.domain_index.cdx_unparseable",
                    target=target.target,
                    bucket=bucket,
                    bytes=len(response.value.body),
                )
            return rows
        ctx.logger.warning(
            "discovery.domain_index.cdx_failed",
            target=target.target,
            bucket=bucket,
            error=response.error.message,
        )
        return None


def _urls(body: bytes) -> list[str] | None:
    """URLs in a CDX JSON body, or ``None`` if the body was not one."""
    try:
        table = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    if not isinstance(table, list):
        return None
    return [
        row[0] for row in table[1:] if isinstance(row, list) and row and isinstance(row[0], str)
    ]


def _cdx_url(target: IndexTarget, bucket: str, limit: int) -> str:
    """The public index query for one slice (exported for tests)."""
    params: dict[str, str] = {
        "url": f"{target.target}/{bucket}" if target.match == "prefix" else target.target,
        "matchType": target.match,
        "fl": "original",
        "collapse": "urlkey",
        "limit": str(limit),
        "output": "json",
    }
    if target.match == "domain":
        params["filter"] = f"original:^https?://{bucket}"
    return f"{CDX_ENDPOINT}?{urlencode(params)}"


def _candidate(connector_id: str, url: str) -> Candidate | None:
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if connector_id == "ats.workday":
        return _workday(host, parts.path)
    if connector_id == "ats.icims":
        return _subdomain_board(
            connector_id, host, _ICIMS_HOST_RE, lambda board_host: f"https://{board_host}"
        )
    if connector_id == "ats.bamboohr":
        return _subdomain_board(
            connector_id,
            host,
            _BAMBOO_HOST_RE,
            lambda board_host: f"https://{board_host}/careers",
        )
    if connector_id == "ats.eightfold":
        return _subdomain_board(
            connector_id,
            host,
            _EIGHTFOLD_HOST_RE,
            lambda board_host: f"https://{board_host}/careers",
        )
    if connector_id == "ats.ultipro":
        return _ultipro(host, parts.path)
    if connector_id == "ats.workable":
        return _workable(host, parts.path)
    return None


def _workday(host: str, path: str) -> Candidate | None:
    match = _WORKDAY_HOST_RE.match(host) or _WORKDAY_PLAIN_RE.match(host)
    if match is None:
        return None
    segments = [segment for segment in path.split("/") if segment]
    locale = ""
    if segments and _LOCALE_RE.match(segments[0]):
        locale, segments = segments[0], segments[1:]
    if not segments:
        return None
    site = segments[0]
    # Indexed URLs include assets (``/favicon.ico``) and API paths
    # (``/wday/cxs/...``), neither of which is a career site.
    if "." in site or site.lower() in _RESERVED_WORKDAY_SITES:
        return None
    board = f"https://{host}/{locale}/{site}" if locale else f"https://{host}/{site}"
    tenant = match.group("tenant")
    return Candidate(
        board,
        tenant,
        tenant.replace("-", " ").title(),
        "ats.workday",
        tenant_key=tenant,
        dedupe_key=f"workday:{tenant.lower()}:{site.lower()}",
    )


def _subdomain_board(
    connector_id: str,
    host: str,
    pattern: re.Pattern[str],
    build: Callable[[str], str],
) -> Candidate | None:
    match = pattern.match(host)
    if match is None:
        return None
    tenant = match.group("tenant")
    if tenant in _RESERVED_LABELS:
        return None
    return Candidate(build(host), tenant, tenant.replace("-", " ").title(), connector_id)


def _ultipro(host: str, path: str) -> Candidate | None:
    if host not in _ULTIPRO_HOSTS:
        return None
    match = _ULTIPRO_PATH_RE.match(path)
    if match is None:
        return None
    tenant = match.group("tenant").upper()
    board = match.group("board").lower()
    hint = None if any(char.isdigit() for char in tenant) else tenant.title()
    return Candidate(
        f"https://{host}/{tenant}/JobBoard/{board}",
        tenant,
        hint,
        "ats.ultipro",
        tenant_key=board,
    )


def _workable(host: str, path: str) -> Candidate | None:
    if host == "apply.workable.com":
        segments = [segment for segment in path.split("/") if segment]
        if not segments:
            return None
        account = segments[0]
        if account in _RESERVED_WORKABLE:
            return None
        return Candidate(
            f"https://apply.workable.com/{account}",
            account,
            account.replace("-", " ").title(),
            "ats.workable",
        )
    suffix = ".workable.com"
    if not host.endswith(suffix):
        return None
    account = host[: -len(suffix)]
    if not account or "." in account or account in _RESERVED_WORKABLE:
        return None
    return Candidate(
        f"https://apply.workable.com/{account}",
        account,
        account.replace("-", " ").title(),
        "ats.workable",
    )
