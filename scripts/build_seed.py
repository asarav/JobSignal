"""Build the bundled ATS seed list from the public web URL index.

The seed file is a *bootstrap*: discovery's engine is the web-index provider
(see ADR 0005), and this script only refreshes the "one time" artifact that
gives a cold install something to crawl immediately.

How it works
------------

1. **Harvest fixed-host tenants.** Ask the Internet Archive CDX API for every
   ``(ATS host, leading-character bucket)`` slice, a few at a time with a
   short gap between requests.  Each request asks for ``limit=10000`` rows,
   which the gateway serves in roughly 15-60 s and which captures a whole
   bucket instead of a small sample.  A per-host ``filter`` drops job-detail
   URLs (``/jobs/123``, ``/{tenant}/{uuid}``), which are what used to fill a
   bucket with a handful of tenants: with the filter, one request covers
   hundreds of boards.  A bucket that still returns a full 10,000 rows is
   split into two-character buckets and walked again, so coverage does not
   stop at whatever the index happens to list first.
2. **Harvest subdomain tenants.** Workday, iCIMS and BambooHR put the employer in the
   subdomain, so a prefix query cannot reach them.  A ``matchType=domain``
   query walks the whole domain in 10,000-row chunks with CDX's resumption
   key, and ``filter`` clauses keep only board roots (dropping the marketing
   site, assets, job detail pages and API paths) so each chunk names many
   more tenants than the raw URL stream would.
3. **Verify.** Ask each candidate's public API whether the board is live:
   Greenhouse/Lever/Ashby/SmartRecruiters expose per-tenant JSON, Workday
   exposes the CxS ``jobs`` endpoint its own site calls, iCIMS exposes a
   search page, BambooHR exposes the ``/careers/list`` JSON its widget calls,
   and UKG Pro/UltiPro exposes its ``LoadSearchResults`` board endpoint.  A
   board with zero postings is still live and stays in the list;
   a board the API does not know is dropped.
4. **Rank.** Write the live boards largest-first, so the cold-start warm-up
   (which registers at most ``discovery.max_new_sources_per_run`` per pass)
   reaches the biggest employers first.

Progress is checkpointed after every slice and every subdomain chunk, so an
interrupted run resumes where it left off.  With ``--incremental`` every
settled board is also remembered (``.seed_build_verified.json``), so a later
expansion pass verifies only boards no earlier pass has seen.  The archive gateway throttles
heavy scans with 503s; the harvester backs off for minutes rather than
abandoning a chunk.

Run it (network required):

    python scripts/build_seed.py                  # harvest, verify, rank
    python scripts/build_seed.py --limit 2000      # keep the top 2,000
    python scripts/build_seed.py --resume          # reuse the harvest cache
    python scripts/build_seed.py --incremental     # verify only boards the
                                                   # shipped file lacks, then
                                                   # merge them into it
    python scripts/build_seed.py --refresh         # no harvest: re-verify and
                                                   # re-rank the shipped file
    python scripts/build_seed.py --skip-fixed      # only walk Workday/iCIMS,
                                                   # keeping the shipped boards
    python scripts/build_seed.py --workday-chunks 0 --icims-chunks 0
                                                   # fixed-host ATSs only

The default chunk counts walk far enough into the Workday/iCIMS/UKG Pro
spaces to name tens of thousands of boards; a full run takes hours and is
resumable at any point (every completed query is checkpointed).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlencode, urlsplit

import httpx

# ``python scripts/build_seed.py`` puts ``scripts/`` on sys.path, not the repo
# root; the app package lives at the root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.plugins.ats.icims import _job_links  # noqa: E402
from app.plugins.discovery.web_index import DEFAULT_ATS_HOSTS, _tenant_of  # noqa: E402

SEEDS_PATH = ROOT / "app" / "plugins" / "discovery" / "seeds" / "ats_tenants.json"
CACHE_PATH = ROOT / ".seed_build_cache.json"
SUBDOMAIN_CACHE_PATH = ROOT / ".seed_build_subdomains.json"
CT_CACHE_PATH = ROOT / ".seed_build_ct.json"
#: Outcome of every board already settled (live posting count, or ``-1`` for a
#: board the ATS does not know), so ``--incremental`` never re-checks them.
VERIFIED_PATH = ROOT / ".seed_build_verified.json"

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
#: Per-request ceiling for an index query.  Logged with every attempt,
#: because "the gateway hung" and "the gateway said 504 after 60 s" need
#: different fixes.
CDX_TIMEOUT_S = 240.0
#: Append-only run log: one JSON object per event, for post-mortems.
LOG_PATH = ROOT / ".seed_build_log.jsonl"
CT_ENDPOINT = "https://crt.sh/"
CT_ATTEMPTS = 4
CT_BACKOFF_S = (20.0, 40.0, 80.0)
BUCKETS = "abcdefghijklmnopqrstuvwxyz0123456789"
CDX_LIMIT = 10000
REQUEST_GAP_S = 2.0
HARVEST_ATTEMPTS = 4
VERIFY_ATTEMPTS = 3
VERIFY_CONCURRENCY = min(32, max(8, (os.cpu_count() or 4) * 4))
#: Index queries in flight at once.  The gateway rejects bursts, so this stays
#: small enough that the per-request backoff can absorb a throttled request.
SLICE_CONCURRENCY = 2
#: How many characters deep a bucket is split before it is accepted as-is.
MAX_BUCKET_DEPTH = 3
#: How long a subdomain walk keeps retrying roots that are all failing before
#: it gives up for this run.  The gateway's "Temporarily Offline" page clears
#: in seconds to a couple of minutes, and abandoning the connector on the first
#: bad pass is what made a healthy walk look like a dead end.
CDX_IDLE_BUDGET_S = 300.0

#: Job-detail URL shapes, which repeat a tenant the bucket already names.
UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

#: Per-host CDX ``filter`` clauses that keep board roots and drop the job
#: pages that otherwise consume a whole 10,000-row bucket for a few tenants.
FIXED_HOST_FILTERS: dict[str, str] = {
    "boards.greenhouse.io": "!original:.*/jobs/.*",
    "job-boards.greenhouse.io": "!original:.*/jobs/.*",
    "jobs.lever.co": f"!original:.*{UUID_RE}.*",
    "jobs.ashbyhq.com": f"!original:.*{UUID_RE}.*",
    "jobs.smartrecruiters.com": "!original:.*/[0-9]{6,}.*",
    "apply.workable.com": "!original:.*/j/.*",
}
#: Backoff before retrying a throttled archive chunk (seconds).  Measured:
#: the gateway answers "Temporarily Offline" in ~5 s and recovers within ~30 s,
#: so minute-scale waits just idle through windows where it is serving again.
THROTTLE_BACKOFF_S = (5.0, 15.0, 45.0, 120.0)

#: Public per-tenant JSON APIs, one per fixed-host connector.
API_TEMPLATES: dict[str, str] = {
    "ats.greenhouse": "https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs",
    "ats.lever": "https://api.lever.co/v0/postings/{tenant}?mode=json",
    "ats.ashby": "https://api.ashbyhq.com/posting-api/job-board/{tenant}",
    "ats.smartrecruiters": (
        "https://api.smartrecruiters.com/v1/companies/{tenant}/postings?limit=1"
    ),
    "ats.workable": "https://apply.workable.com/api/v1/widget/accounts/{tenant}",
}

#: Fixed hosts the seed builder walks.  Workable is path-addressed like the
#: others but is not part of the runtime rotation, so it is added here
#: instead of to ``web_index.DEFAULT_ATS_HOSTS``.
SEED_ATS_HOSTS: tuple[tuple[str, str], ...] = (
    *DEFAULT_ATS_HOSTS,
    ("apply.workable.com", "ats.workable"),
)

#: Hosts whose tenant is a subdomain; each is walked with a domain query.
#: ``filters`` keep board roots only: a tenant can own tens of thousands of
#: asset, job-detail and API URLs, and every one of them used to crowd a chunk
#: that could have named a hundred new employers instead.
class _SubdomainCfg(TypedDict):
    domain: str
    #: Query roots, walked one chunk at a time in turn.  A shard-specific root
    #: keeps a chunk inside one shard's tenant range, where the apex walk spent
    #: it on the few employers it met first.
    roots: tuple[str, ...]
    filters: tuple[str, ...]


#: Workday shards.  Boards live on ``{tenant}.{shard}.myworkdayjobs.com``, so
#: querying a shard groups ten thousand rows over thousands of employers while
#: the same rows taken from the apex name a dozen.
#: Every shard number Workday hands out.  Tenants are spread across them, so
#: each shard is walked in turn instead of exhausting the first one.
WORKDAY_SHARDS = (
    "wd1", "wd2", "wd3", "wd4", "wd5",
    "wd6", "wd7", "wd8", "wd9", "wd10", "wd11", "wd12", "wd13", "wd14", "wd15",
)


SUBDOMAIN_ATS: dict[str, _SubdomainCfg] = {
    "ats.workday": {
        "domain": "myworkdayjobs.com",
        # Shard roots only: the apex answers the same rows filtered by a
        # different host, and once its first chunks are read the rest are
        # tenants already seen (measured: 10,000 rows, +0 boards, 133 s).
        "roots": tuple(f"{shard}.myworkdayjobs.com" for shard in WORKDAY_SHARDS),
        "filters": (
            # A board-root include filter (`/[locale]/[site]`) reads better but
            # makes the archive gateway answer 504: the filter scan itself
            # times out on this domain.  Excluding job details and assets is
            # what the gateway can serve, so rows are reduced in memory.
            "!original:.*/assets/.*",
            "!original:.*/job/.*",
        ),
    },
    "ats.bamboohr": {
        "domain": "bamboohr.com",
        "roots": ("bamboohr.com",),
        "filters": (
            # BambooHR's own marketing site is not a customer board.  Every row
            # is reduced to its subdomain in memory, so job-detail URLs need no
            # clause of their own -- and a second clause makes the gateway 504
            # on this domain, which answers 529 tenants per 10,000 rows.
            "!original:.*://www[0-9]*[.]bamboohr[.]com.*",
        ),
    },
    "ats.icims": {
        "domain": "icims.com",
        "roots": ("icims.com",),
        "filters": (
            # www is iCIMS' own marketing site, not a customer board.
            "!original:.*://www[0-9]*\\.icims\\.com.*",
            "!original:.*/jobs/.*",
        ),
    },
}


#: UKG Pro / UltiPro boards are path-based: the employer code and board id
#: both live in the path (``/{tenant}/JobBoard/{board_id}``), on several hosts.
ULTIPRO_HOSTS = (
    "recruiting.ultipro.com",
    "recruiting2.ultipro.com",
    "recruiting3.ultipro.com",
    "recruiting.ultipro.ca",
)
ULTIPRO_DOMAIN = "recruiting.ultipro.com"
ULTIPRO_PATH_RE = re.compile(
    r"^/([A-Za-z0-9]{2,32})/JobBoard/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:/|$)"
)

RESERVED_SUBDOMAINS = frozenset(
    {
        "www",
        "www1",
        "www2",
        "www3",
        "www4",
        "www5",
        "www6",
        "api",
        "app",
        "assets",
        "cdn",
        "connect",
        "cs",
        "help",
        "info",
        "jobs",
        "careers",
        "login",
        "secure",
        "static",
        "status",
        "support",
        # Certificate-transparency hits that are iCIMS/Workday infrastructure,
        # not customer boards.
        "autodiscover",
        "hrinsights",
        "img",
        "images",
        "mail",
        "media",
        "myworkdayjobs",
        "myworkdaysite",
        "smtp",
    }
)
#: BambooHR subdomains that are telemetry/asset infrastructure, not customers.
BAMBOOHR_RESERVED_PREFIXES = ("adobe-", "analytics-", "cdn-", "telemetry-")
TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
LOCALE_RE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2,4})?$")
WORKDAY_SHARD_RE = re.compile(r"^(?:impl-)?wd\d+$")
#: A Workday site name as it appears in a career-site path (``Careers``,
#: ``Acrisure``); chrome such as ``favicon.ico`` or ``.well-known`` is not.
SITE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
#: First path segments that are site plumbing, not a career site.
RESERVED_SITES = frozenset(
    {"assets", "static", "media", "images", "css", "js", "fonts", "login", "logout"}
)
#: Crawlers that captured a protocol-relative link store its escaped
#: slashes (``%2f``) as the literal text ``252f`` in front of the host,
#: which hides a tenant that is otherwise perfectly good.
MANGLED_HOST_RE = re.compile(r"^(?:252f)+")


@dataclass(frozen=True, slots=True)
class Board:
    """One candidate board, independent of how it was harvested."""

    connector_id: str
    tenant: str
    url: str
    tenant_key: str | None = None

    @property
    def dedupe_key(self) -> tuple[str, ...]:
        """The *board's* identity, which is not always the employer's.

        A Workday tenant can run several career sites (``alterra`` has one per
        resort brand), each with its own postings behind its own CxS endpoint,
        so collapsing on the tenant alone silently dropped whole boards.  The
        site segment distinguishes them; the locale prefix and the ``wd{N}``
        shard do not, since ``/en-US/Careers`` and ``/fr-CA/Careers`` (or the
        same site mirrored on two shards) are one board.  This is the same key
        runtime discovery uses: ``workday:{tenant}:{site}``.
        """
        if self.connector_id == "ats.workday":
            site = _workday_site(self.url) or ""
            return (self.connector_id, (self.tenant_key or self.tenant).lower(), site.lower())
        return (self.connector_id, self.tenant_key or self.tenant)


def _host_for(connector_id: str) -> str | None:
    """The preferred fixed host for a connector (first in the rotation)."""
    return next((host for host, conn in SEED_ATS_HOSTS if conn == connector_id), None)


def _board_url(connector_id: str, tenant: str) -> str:
    host = _host_for(connector_id) or SEED_ATS_HOSTS[0][0]
    return f"https://{host}/{tenant}"


def _count_jobs(connector_id: str, data: object) -> int:
    """Posting count from a board's public JSON API response.

    The Ashby posting API answers ``{"jobs": [...], "apiVersion": "1"}`` (the
    same shape the M1 connector reads); older examples of a ``jobBoard``
    wrapper are tolerated but not required.
    """
    if connector_id == "ats.greenhouse":
        return len(data.get("jobs", [])) if isinstance(data, dict) else 0
    if connector_id == "ats.lever":
        return len(data) if isinstance(data, list) else 0
    if connector_id == "ats.ashby":
        if not isinstance(data, dict):
            return 0
        jobs = data.get("jobs")
        if isinstance(jobs, list):
            return len(jobs)
        board = data.get("jobBoard", {})
        postings = board.get("jobPostings", []) if isinstance(board, dict) else []
        return len(postings) if isinstance(postings, list) else 0
    if connector_id == "ats.smartrecruiters":
        return int(data.get("totalFound", 0)) if isinstance(data, dict) else 0
    if connector_id == "ats.workable":
        return len(data.get("jobs", [])) if isinstance(data, dict) else 0
    return 0


# --------------------------------------------------------------------- logging


def _preview(body: str, limit: int = 160) -> str:
    """A short single-line excerpt of a response body, for the log."""
    return " ".join(body[:limit].split())


def _log(event: str, **fields: object) -> None:
    """One timestamped line on stdout, plus a JSON row for later analysis.

    Every network attempt, chunk and decision goes through here: a run that
    fails unattended should be diagnosable from ``.seed_build_log.jsonl``
    alone, without re-running it and watching.
    """
    detail = " ".join(f"{key}={value}" for key, value in fields.items())
    print(f"{time.strftime('%H:%M:%S')} {event} {detail}".rstrip(), flush=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "at": datetime.now(UTC).isoformat(timespec="seconds"),
                        "event": event,
                        **fields,
                    }
                )
                + "\n"
            )
    except OSError:
        pass


# --------------------------------------------------------------- CDX plumbing


async def _cdx_get(
    client: httpx.AsyncClient, params: list[tuple[str, str]], purpose: str = ""
) -> httpx.Response | None:
    """One CDX request with patient backoff while the gateway throttles.

    Every attempt is logged with its query shape, status, body size and elapsed
    time: an unanswered request, a fast 504 and a slow 504 are different
    failures, and the log has to tell them apart.
    """
    query = f"{CDX_ENDPOINT}?{urlencode(params)}"
    options = dict(params)
    requested = {
        "url": params[0][1] if params else "",
        "match_type": options.get("matchType", ""),
        "limit": options.get("limit", ""),
        "filters": [value for key, value in params if key == "filter"],
        "has_resume_key": "resumeKey" in options,
    }
    for attempt in range(HARVEST_ATTEMPTS):
        started = time.monotonic()
        try:
            response = await client.get(query, timeout=CDX_TIMEOUT_S)
        except httpx.HTTPError as exc:
            _log(
                "cdx_error",
                purpose=purpose,
                attempt=attempt + 1,
                error=type(exc).__name__,
                elapsed_s=round(time.monotonic() - started, 1),
                **requested,
            )
        else:
            elapsed = round(time.monotonic() - started, 1)
            if response.status_code == 200:
                _log(
                    "cdx_ok",
                    purpose=purpose,
                    attempt=attempt + 1,
                    bytes=len(response.content),
                    elapsed_s=elapsed,
                    **requested,
                )
                return response
            retryable = response.status_code in (429, 500, 502, 503, 504)
            wait = THROTTLE_BACKOFF_S[attempt] if attempt + 1 < HARVEST_ATTEMPTS else 0
            _log(
                "cdx_status",
                purpose=purpose,
                attempt=attempt + 1,
                status=response.status_code,
                bytes=len(response.content),
                elapsed_s=elapsed,
                retry_in_s=wait if retryable else 0,
                body=_preview(response.text),
                **requested,
            )
            if not retryable:
                return None
        if attempt + 1 < HARVEST_ATTEMPTS:
            await asyncio.sleep(THROTTLE_BACKOFF_S[attempt])
    _log("cdx_gave_up", purpose=purpose, attempts=HARVEST_ATTEMPTS, **requested)
    return None


def _cdx_tenants(body: str, host: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    try:
        table = json.loads(body)
    except ValueError:
        return {}
    if not isinstance(table, list):
        return {}
    for record in table[1:]:
        if not isinstance(record, list) or not record:
            continue
        original = record[0]
        if not isinstance(original, str):
            continue
        tenant = _tenant_of(original, host)
        if tenant is not None:
            counts[tenant] += 1
    return dict(counts)


def _row_count(body: str) -> int | None:
    """How many result rows a CDX response carries (header excluded).

    ``None`` means the body was not the JSON table the index promises: an HTML
    error page, a truncated body, an empty response.  That is not the same as
    a valid empty result, and callers must not record it as one.
    """
    try:
        table = json.loads(body)
    except ValueError:
        return None
    if not isinstance(table, list):
        return None
    return max(0, len(table) - 1)


async def harvest_slice(
    client: httpx.AsyncClient,
    host: str,
    bucket: str,
    sem: asyncio.Semaphore,
) -> tuple[int, dict[str, int]] | None:
    """``(rows seen, {tenant: url_count})`` for one ``(host, bucket)`` slice.

    The row count matters: a slice that returns a full ``CDX_LIMIT`` has been
    truncated by the gateway, so its bucket is split rather than trusted.
    ``None`` means the index did not answer, so the slice stays unfinished and
    a later run retries it instead of recording an empty bucket.
    """
    params = [
        ("url", f"{host}/{bucket}"),
        ("matchType", "prefix"),
        ("fl", "original"),
        ("collapse", "urlkey"),
        ("limit", str(CDX_LIMIT)),
        ("output", "json"),
    ]
    host_filter = FIXED_HOST_FILTERS.get(host)
    if host_filter:
        params.append(("filter", host_filter))
    async with sem:
        await asyncio.sleep(REQUEST_GAP_S)
        response = await _cdx_get(client, params, purpose=f"slice {host}/{bucket}")
    if response is None:
        return None
    rows = _row_count(response.text)
    if rows is None:
        _log(
            "slice_unparsed",
            host=host,
            bucket=bucket,
            bytes=len(response.content),
            body=_preview(response.text),
        )
        return None
    counts = _cdx_tenants(response.text, host)
    _log("slice_rows", host=host, bucket=bucket, rows=rows, tenants=len(counts))
    return rows, counts


def _read_harvest_cache() -> tuple[set[tuple[str, str]], dict[Board, int]]:
    """Checkpointed slices and candidates from an interrupted run."""
    completed: set[tuple[str, str]] = set()
    boards: dict[Board, int] = defaultdict(int)
    if not CACHE_PATH.exists():
        return completed, dict(boards)
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return completed, dict(boards)
    if not isinstance(raw, dict) or "results" not in raw:
        return completed, dict(boards)
    for key, value in (raw.get("results") or {}).items():
        parts = str(key).split("|", 1)
        if len(parts) == 2:
            boards[_board_for(parts[0], parts[1])] += int(value)
    for pair in raw.get("completed") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            completed.add((str(pair[0]), str(pair[1])))
    return completed, dict(boards)


def _checkpoint(completed: set[tuple[str, str]], boards: dict[Board, int]) -> None:
    CACHE_PATH.write_text(
        json.dumps(
            {
                "completed": [[host, bucket] for host, bucket in sorted(completed)],
                "results": {
                    f"{board.connector_id}|{board.tenant}": count
                    for board, count in boards.items()
                },
            },
            indent=1,
        ),
        encoding="utf-8",
    )


async def harvest_all(client: httpx.AsyncClient, resume: bool) -> dict[Board, int]:
    """Harvest every fixed-host slice, reusing checkpointed results.

    Slices run a few at a time, and a slice the gateway truncated (a full
    ``CDX_LIMIT`` rows) is split into two-character buckets and walked again,
    so a single crowded bucket cannot hide the long tail behind it.
    """
    completed, cached = _read_harvest_cache()
    boards: dict[Board, int] = defaultdict(int, cached)
    if completed:
        _log("harvest_resume", slices=len(completed), candidates=len(boards))
        if resume:
            return boards

    queue: list[tuple[str, str]] = [
        (host, bucket)
        for host, _connector_id in SEED_ATS_HOSTS
        for bucket in BUCKETS
        if (host, bucket) not in completed
    ]
    done = len(completed)
    started = time.monotonic()
    semaphore = asyncio.Semaphore(SLICE_CONCURRENCY)

    async def run_one(host: str, bucket: str) -> tuple[str, str, int, bool]:
        connector_id = next(connector for h, connector in SEED_ATS_HOSTS if h == host)
        result = await harvest_slice(client, host, bucket, semaphore)
        if result is None:
            # Left out of ``completed`` on purpose: the next run retries it.
            return host, bucket, -1, False
        rows, counts = result
        for tenant, count in counts.items():
            boards[_board_for(connector_id, tenant)] += count
        completed.add((host, bucket))
        truncated = rows >= CDX_LIMIT and len(bucket) < MAX_BUCKET_DEPTH
        return host, bucket, rows, truncated

    while queue:
        batch = queue[:SLICE_CONCURRENCY]
        del queue[:SLICE_CONCURRENCY]
        outcomes = await asyncio.gather(
            *(run_one(host, bucket) for host, bucket in batch), return_exceptions=True
        )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                _log("slice_failed", error=type(outcome).__name__)
                continue
            host, bucket, rows, truncated = outcome
            if rows < 0:
                _log("slice_unanswered", host=host, bucket=bucket, retry="next run")
                continue
            done += 1
            if truncated:
                _log("slice_truncated", host=host, bucket=bucket, rows=rows)
                queue.extend(
                    (host, bucket + char)
                    for char in BUCKETS
                    if (host, bucket + char) not in completed
                )
        _checkpoint(completed, boards)
        _log(
            "harvest_progress",
            slices=done,
            candidates=len(boards),
            queued=len(queue),
            elapsed_s=round(time.monotonic() - started),
        )
    _checkpoint(completed, boards)
    return boards


def _workday_site(url: str) -> str | None:
    """The career site a Workday board URL points at.

    Workday serves ``/{locale}/{site}`` and a locale-less ``/{site}``; the site
    is what separates one board from another on the same tenant, so reading
    ``segments[1]`` blindly made every locale-less board collide on ``""``.
    """
    segments = [part for part in urlsplit(url).path.split("/") if part]
    if not segments:
        return None
    if LOCALE_RE.match(segments[0]):
        return segments[1] if len(segments) > 1 else None
    return segments[0]


def _board_for(connector_id: str, tenant: str) -> Board:
    return Board(connector_id, tenant, _board_url(connector_id, tenant))


# ---------------------------------------------------------- subdomain harvest


def _subdomain_board(connector_id: str, url: str) -> Board | None:
    """A board derived from one indexed URL, or ``None`` for site chrome."""
    parts = urlsplit(url)
    host = MANGLED_HOST_RE.sub("", parts.netloc.lower())
    cfg = SUBDOMAIN_ATS[connector_id]
    suffix = "." + cfg["domain"]
    if not host.endswith(suffix):
        return None
    sub = host[: -len(suffix)]
    labels = sub.split(".")
    if connector_id == "ats.workday":
        # {tenant}.wd{N}.myworkdayjobs.com or {tenant}.myworkdayjobs.com
        if len(labels) == 1:
            tenant = labels[0]
        elif len(labels) == 2 and WORKDAY_SHARD_RE.match(labels[1]):
            tenant = labels[0]
        else:
            return None
        if not TENANT_RE.match(tenant):
            return None
        segments = [segment for segment in parts.path.split("/") if segment]
        if not segments:
            return None
        # Workday serves both ``/{locale}/{site}`` and a locale-less
        # ``/{site}``.  Requiring the locale dropped every board whose indexed
        # URLs only take the second form -- a whole tenant at a time.
        if LOCALE_RE.match(segments[0]):
            if len(segments) < 2:
                return None
            path = f"/{segments[0]}/{segments[1]}"
        elif segments[0].lower() in RESERVED_SITES:
            return None
        else:
            path = f"/{segments[0]}"
        if not SITE_RE.match(path.rsplit("/", 1)[-1]):
            return None
        return Board(connector_id, tenant, f"https://{host}{path}", tenant_key=tenant)
    if connector_id == "ats.icims":
        if len(labels) != 1 or "." in sub:
            return None
        if sub in RESERVED_SUBDOMAINS or not TENANT_RE.match(sub):
            return None
        return Board(connector_id, sub, f"https://{host}")
    if connector_id == "ats.bamboohr":
        if len(labels) != 1 or "." in sub:
            return None
        if sub in RESERVED_SUBDOMAINS or not TENANT_RE.match(sub):
            return None
        if sub.startswith(BAMBOOHR_RESERVED_PREFIXES):
            return None
        # The tenant key keeps the company name from collapsing to "Careers"
        # (the last path segment) and is what the connector reads.
        return Board(connector_id, sub, f"https://{host}/careers", tenant_key=sub)
    return None


def _ultipro_board(url: str) -> Board | None:
    """A UKG Pro board from one indexed URL, or ``None`` for site chrome."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host not in ULTIPRO_HOSTS:
        return None
    match = ULTIPRO_PATH_RE.match(parts.path)
    if match is None:
        return None
    tenant = match.group(1).upper()
    board_id = match.group(2).lower()
    return Board(
        "ats.ultipro",
        tenant,
        f"https://{host}/{tenant}/JobBoard/{board_id}",
        tenant_key=board_id,
    )


def _chunk_urls(body: str) -> tuple[list[str], str | None, bool]:
    """URLs in a CDX JSON chunk, its resumption key, and whether it parsed.

    The third value separates "the index says there is nothing more" from
    "this body was not the JSON table at all".  Only the former may end a
    root's walk; treating a garbled body as the end silently loses a shard.
    """
    try:
        table = json.loads(body)
    except ValueError:
        return [], None, False
    if not isinstance(table, list):
        return [], None, False
    key: str | None = None
    if len(table) >= 2 and table[-2] == [] and isinstance(table[-1], list):
        key = table[-1][0] if table[-1] else None
        table = table[:-2]
    urls = [
        row[0] for row in table[1:] if isinstance(row, list) and row and isinstance(row[0], str)
    ]
    return urls, key, True


def _open_roots(cfg: _SubdomainCfg, root_state: dict[str, dict[str, object]]) -> list[str]:
    """Roots whose walk has not reached the end of the captured rows."""
    return [root for root in cfg["roots"] if not root_state.get(root, {}).get("done")]


async def harvest_subdomains(
    client: httpx.AsyncClient,
    connector_id: str,
    chunks: int,
    resume: bool,
) -> dict[Board, int]:
    """Walk a subdomain-based ATS in resumable 10,000-row chunks.

    Roots are walked one chunk at a time in turn, so a run spreads over the
    whole tenant space instead of exhausting the first crowded one, and each
    root keeps its own resume key, so an interrupted run continues where it
    stopped.
    """
    cfg = SUBDOMAIN_ATS[connector_id]
    cache: dict[str, object] = {}
    if SUBDOMAIN_CACHE_PATH.exists():
        try:
            raw = json.loads(SUBDOMAIN_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cache = raw
        except (OSError, ValueError):
            cache = {}
    stored = cache.get(connector_id)
    state: dict[str, object] = stored if isinstance(stored, dict) else {}

    boards: dict[Board, int] = defaultdict(int)
    stored_boards = state.get("boards")
    for key, count in (stored_boards if isinstance(stored_boards, dict) else {}).items():
        parts = str(key).split("|", 3)
        if len(parts) == 4:
            boards[Board(parts[0], parts[1], parts[2], parts[3] or None)] += int(count)

    root_state: dict[str, dict[str, object]] = {}
    stored_roots = state.get("roots")
    for root, item in (stored_roots if isinstance(stored_roots, dict) else {}).items():
        if isinstance(item, dict):
            root_state[str(root)] = {str(k): v for k, v in item.items()}

    stored_chunks = state.get("chunks", 0)
    done = stored_chunks if isinstance(stored_chunks, int) else 0
    if resume and done and (done >= chunks or not _open_roots(cfg, root_state)):
        _log("resume_cached", connector=connector_id, chunks=done, candidates=len(boards))
        return dict(boards)

    def save() -> None:
        cache[connector_id] = {
            "chunks": done,
            "roots": root_state,
            "boards": {
                f"{b.connector_id}|{b.tenant}|{b.url}|{b.tenant_key or ''}": n
                for b, n in boards.items()
            },
        }
        SUBDOMAIN_CACHE_PATH.write_text(json.dumps(cache, indent=1), encoding="utf-8")

    started = time.monotonic()
    idle_since: float | None = None
    while done < chunks:
        open_roots = _open_roots(cfg, root_state)
        if not open_roots:
            _log("roots_exhausted", connector=connector_id, chunks=done)
            break
        progressed = False
        for root in open_roots:
            if done >= chunks:
                break
            stored_key = root_state.get(root, {}).get("key")
            resume_key = stored_key if isinstance(stored_key, str) and stored_key else None
            params: list[tuple[str, str]] = [
                ("url", root),
                ("matchType", "domain"),
                ("fl", "original"),
                ("collapse", "urlkey"),
                ("limit", str(CDX_LIMIT)),
                ("output", "json"),
                ("showResumeKey", "true"),
            ]
            params.extend(("filter", clause) for clause in cfg["filters"])
            if resume_key:
                params.append(("resumeKey", resume_key))
            _log(
                "chunk_start",
                connector=connector_id,
                root=root,
                chunk=f"{done + 1}/{chunks}",
                has_resume_key=bool(resume_key),
            )
            await asyncio.sleep(REQUEST_GAP_S)
            response = await _cdx_get(client, params, purpose=f"{connector_id} {root}")
            if response is None:
                _log("chunk_unanswered", connector=connector_id, root=root, retry="next run")
                continue
            urls, next_key, parsed = _chunk_urls(response.text)
            if not parsed:
                # Keep the root open: an unparseable body says nothing about
                # how many rows are left, and closing it would lose the shard.
                _log(
                    "chunk_unparsed",
                    connector=connector_id,
                    root=root,
                    bytes=len(response.content),
                    body=_preview(response.text),
                )
                continue
            before = len(boards)
            for url in urls:
                candidate = _subdomain_board(connector_id, url)
                if candidate is not None:
                    boards[candidate] += 1
            done += 1
            progressed = True
            ended = next_key is None
            root_state[root] = {"key": next_key or "", "done": ended}
            save()
            _log(
                "chunk_done",
                connector=connector_id,
                root=root,
                chunk=f"{done}/{chunks}",
                rows=len(urls),
                added=len(boards) - before,
                candidates=len(boards),
                ended=ended,
                elapsed_s=round(time.monotonic() - started),
            )
        if not progressed:
            now = time.monotonic()
            idle_since = now if idle_since is None else idle_since
            idle = now - idle_since
            if idle >= CDX_IDLE_BUDGET_S:
                _log(
                    "roots_abandoned",
                    connector=connector_id,
                    chunks=done,
                    idle_s=round(idle),
                )
                break
            _log(
                "roots_retrying",
                connector=connector_id,
                chunks=done,
                idle_s=round(idle),
                budget_s=CDX_IDLE_BUDGET_S,
            )
            continue
        idle_since = None
    return dict(boards)


async def harvest_ultipro(
    client: httpx.AsyncClient, chunks: int, resume: bool
) -> dict[Board, int]:
    """Walk UKG Pro's path-based boards in resumable 10,000-row chunks."""
    cache: dict[str, object] = {}
    if SUBDOMAIN_CACHE_PATH.exists():
        try:
            raw = json.loads(SUBDOMAIN_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cache = raw
        except (OSError, ValueError):
            cache = {}
    stored = cache.get("ats.ultipro")
    state: dict[str, object] = stored if isinstance(stored, dict) else {}

    boards: dict[Board, int] = defaultdict(int)
    stored_boards = state.get("boards")
    for key, count in (stored_boards if isinstance(stored_boards, dict) else {}).items():
        parts = str(key).split("|", 3)
        if len(parts) == 4:
            boards[Board(parts[0], parts[1], parts[2], parts[3] or None)] += int(count)
    stored_key = state.get("key")
    resume_key: str | None = stored_key if isinstance(stored_key, str) else None
    stored_chunks = state.get("chunks", 0)
    done = stored_chunks if isinstance(stored_chunks, int) else 0
    if resume and done and (not resume_key or done >= chunks):
        _log("resume_cached", connector="ats.ultipro", chunks=done, candidates=len(boards))
        return dict(boards)

    started = time.monotonic()
    while done < chunks:
        params: list[tuple[str, str]] = [
            ("url", ULTIPRO_DOMAIN),
            ("matchType", "domain"),
            ("fl", "original"),
            ("collapse", "urlkey"),
            ("limit", str(CDX_LIMIT)),
            ("output", "json"),
            ("showResumeKey", "true"),
            ("filter", "original:.*/JobBoard/.*"),
        ]
        if resume_key:
            params.append(("resumeKey", resume_key))
        _log(
            "chunk_start",
            connector="ats.ultipro",
            root=ULTIPRO_DOMAIN,
            chunk=f"{done + 1}/{chunks}",
            has_resume_key=bool(resume_key),
        )
        await asyncio.sleep(REQUEST_GAP_S)
        response = await _cdx_get(client, params, purpose=f"ats.ultipro {ULTIPRO_DOMAIN}")
        if response is None:
            _log("chunk_unanswered", connector="ats.ultipro", root=ULTIPRO_DOMAIN, retry="now")
            break
        urls, next_resume_key, parsed = _chunk_urls(response.text)
        if not parsed:
            _log(
                "chunk_unparsed",
                connector="ats.ultipro",
                root=ULTIPRO_DOMAIN,
                bytes=len(response.content),
                body=_preview(response.text),
            )
            break
        resume_key = next_resume_key
        before = len(boards)
        for url in urls:
            candidate = _ultipro_board(url)
            if candidate is not None:
                boards[candidate] += 1
        done += 1
        cache["ats.ultipro"] = {
            "chunks": done,
            "key": resume_key,
            "boards": {
                f"{b.connector_id}|{b.tenant}|{b.url}|{b.tenant_key or ''}": n
                for b, n in boards.items()
            },
        }
        SUBDOMAIN_CACHE_PATH.write_text(json.dumps(cache, indent=1), encoding="utf-8")
        _log(
            "chunk_done",
            connector="ats.ultipro",
            root=ULTIPRO_DOMAIN,
            chunk=f"{done}/{chunks}",
            rows=len(urls),
            added=len(boards) - before,
            candidates=len(boards),
            ended=not resume_key,
            elapsed_s=round(time.monotonic() - started),
        )
        if not resume_key:
            _log("roots_exhausted", connector="ats.ultipro", chunks=done)
            break
    return dict(boards)


# ------------------------------------------------- certificate transparency


def _ct_hosts(entries: list[object]) -> set[str]:
    """Every hostname named by a crt.sh certificate entry."""
    hosts: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        values = [entry.get("common_name") or ""]
        values.extend(str(entry.get("name_value") or "").splitlines())
        for value in values:
            host = str(value).strip().lower().lstrip("*.").rstrip(".")
            if host:
                hosts.add(host)
    return hosts


async def _ct_entries(
    client: httpx.AsyncClient, query: str, sem: asyncio.Semaphore
) -> list[object]:
    """One crt.sh query with patient backoff (it 502s under load)."""
    async with sem:
        for attempt in range(CT_ATTEMPTS):
            started = time.monotonic()
            try:
                response = await client.get(
                    CT_ENDPOINT,
                    params={"q": query, "output": "json", "exclude": "expired"},
                    timeout=240.0,
                )
            except httpx.HTTPError as exc:
                _log(
                    "ct_error",
                    query=query,
                    attempt=attempt + 1,
                    error=type(exc).__name__,
                    elapsed_s=round(time.monotonic() - started, 1),
                )
            else:
                elapsed = round(time.monotonic() - started, 1)
                if response.status_code == 200:
                    try:
                        data = response.json()
                    except ValueError:
                        data = None
                    if isinstance(data, list):
                        _log(
                            "ct_ok",
                            query=query,
                            attempt=attempt + 1,
                            certificates=len(data),
                            bytes=len(response.content),
                            elapsed_s=elapsed,
                        )
                        return data
                    _log(
                        "ct_unparsed",
                        query=query,
                        attempt=attempt + 1,
                        bytes=len(response.content),
                        body=_preview(response.text),
                        elapsed_s=elapsed,
                    )
                else:
                    retryable = response.status_code in (429, 500, 502, 503, 504)
                    _log(
                        "ct_status",
                        query=query,
                        attempt=attempt + 1,
                        status=response.status_code,
                        bytes=len(response.content),
                        elapsed_s=elapsed,
                        retry_in_s=CT_BACKOFF_S[attempt]
                        if retryable and attempt + 1 < CT_ATTEMPTS
                        else 0,
                        body=_preview(response.text),
                    )
                    if not retryable:
                        return []
            if attempt + 1 < CT_ATTEMPTS:
                await asyncio.sleep(CT_BACKOFF_S[attempt])
    return []


def _certificate_tenant(connector_id: str, host: str) -> str | None:
    """The board tenant named by a certificate hostname, if any."""
    suffix = "." + SUBDOMAIN_ATS[connector_id]["domain"]
    if not host.endswith(suffix):
        return None
    labels = host[: -len(suffix)].split(".")
    if connector_id == "ats.workday":
        if len(labels) == 1:
            tenant = labels[0]
        elif len(labels) == 2 and WORKDAY_SHARD_RE.match(labels[1]):
            tenant = labels[0]
        else:
            return None
    else:
        if len(labels) != 1:
            return None
        tenant = labels[0]
    if tenant in RESERVED_SUBDOMAINS or not TENANT_RE.match(tenant):
        return None
    return tenant


def _read_ct_cache() -> dict[str, object]:
    try:
        raw = json.loads(CT_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _ct_cached_boards(state: dict[str, object]) -> dict[Board, int]:
    boards: dict[Board, int] = defaultdict(int)
    stored = state.get("boards")
    if not isinstance(stored, dict):
        return {}
    for key, count in stored.items():
        parts = str(key).split("|", 3)
        if len(parts) == 4:
            board = Board(parts[0], parts[1], parts[2], parts[3] or None)
            boards[board] += int(count)
    return dict(boards)


async def harvest_certificates(
    client: httpx.AsyncClient, connector_id: str, resume: bool
) -> dict[Board, int]:
    """Enumerate tenants from public certificate-transparency logs.

    Certificate logs name every hostname a tenant ever served over TLS, so a
    ``*.icims.com`` query yields its customer subdomains without walking the
    archive's URL index.  The logs include expired and staging names, so the
    result is only a candidate list: every board is still verified before it is
    written.  The host set is cached, because crt.sh rate-limits.
    """
    cache = _read_ct_cache()
    stored = cache.get(connector_id)
    state: dict[str, object] = stored if isinstance(stored, dict) else {}
    boards = _ct_cached_boards(state)
    if resume and boards:
        _log("ct_cached", connector=connector_id, boards=len(boards))
        return boards

    hosts = {str(host) for host in (state.get("hosts") or []) if isinstance(host, str)}
    if not hosts:
        domain = SUBDOMAIN_ATS[connector_id]["domain"]
        entries: list[object] = []
        sem = asyncio.Semaphore(1)
        _log("ct_query", domain=domain)
        entries = await _ct_entries(client, f"%.{domain}", sem)
        hosts = _ct_hosts(entries)
        _log("ct_hosts", connector=connector_id, hosts=len(hosts), certificates=len(entries))
        if not hosts:
            return {}
        cache[connector_id] = {"hosts": sorted(hosts)}
        CT_CACHE_PATH.write_text(json.dumps(cache, indent=1), encoding="utf-8")

    tenants: dict[str, str] = {}
    for host in sorted(hosts):
        tenant = _certificate_tenant(connector_id, host)
        if tenant is not None:
            tenants.setdefault(tenant, host)
    _log("ct_tenants", connector=connector_id, tenants=len(tenants))

    boards = {Board(connector_id, tenant, f"https://{host}"): 1 for tenant, host in tenants.items()}
    cache[connector_id] = {
        "hosts": sorted(hosts),
        "boards": {
            f"{b.connector_id}|{b.tenant}|{b.url}|{b.tenant_key or ''}": n
            for b, n in boards.items()
        },
    }
    CT_CACHE_PATH.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    return boards


# ----------------------------------------------------------------- verification


#: ``verify_board`` result meaning "the ATS never gave a usable answer": the
#: board is neither live nor dead, so a later run retries it.
_UNREACHABLE = -1


async def _verify_workday(
    client: httpx.AsyncClient, board: Board, sem: asyncio.Semaphore
) -> int | None:
    """Live Workday board: the CxS endpoint its own frontend calls."""
    parts = urlsplit(board.url)
    site = _workday_site(board.url)
    if site is None:
        return None
    tenant = board.tenant_key or board.tenant
    url = f"https://{parts.netloc}/wday/cxs/{tenant}/{site}/jobs"
    async with sem:
        await asyncio.sleep(0.05)
        for attempt in range(VERIFY_ATTEMPTS):
            try:
                response = await client.post(
                    url,
                    json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""},
                    headers={"content-type": "application/json"},
                    timeout=20.0,
                    follow_redirects=True,
                )
            except httpx.HTTPError:
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError:
                    return 0
                if isinstance(data, dict):
                    total = data.get("total")
                    if isinstance(total, int):
                        return total
                    postings = data.get("jobPostings")
                    return len(postings) if isinstance(postings, list) else 0
                return 0
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            return None
    return _UNREACHABLE


async def _verify_icims(
    client: httpx.AsyncClient, board: Board, sem: asyncio.Semaphore
) -> int | None:
    """Live iCIMS board: its search page answers on the tenant subdomain.

    Current iCIMS portals render the postings inside an iframe, so the plain
    search URL is a shell with no links.  A tenant that redirects to
    ``login.icims.com`` is an employee portal, not a public board.
    """
    host = urlsplit(board.url).netloc.lower()
    async with sem:
        await asyncio.sleep(0.05)
        for attempt in range(VERIFY_ATTEMPTS):
            try:
                response = await client.get(
                    f"{board.url}/jobs/search?ss=1&in_iframe=1",
                    timeout=20.0,
                    follow_redirects=True,
                )
            except httpx.HTTPError:
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code == 200:
                if urlsplit(str(response.url)).netloc.lower() != host:
                    return None
                return len(_job_links(response.text, str(response.url)))
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            return None
    return _UNREACHABLE


async def _verify_bamboohr(
    client: httpx.AsyncClient, board: Board, sem: asyncio.Semaphore
) -> int | None:
    """Live BambooHR board: the company subdomain serves its own JSON feed.

    A company BambooHR does not know redirects to ``www.bamboohr.com`` and
    answers **200 with the marketing site**, so neither the status code nor a
    zero count proves the board exists: the redirect has to stay on the
    tenant's own host and the body has to be the widget's JSON.
    """
    host = urlsplit(board.url).netloc.lower()
    async with sem:
        await asyncio.sleep(0.05)
        for attempt in range(VERIFY_ATTEMPTS):
            try:
                response = await client.get(
                    f"https://{host}/careers/list",
                    timeout=20.0,
                    follow_redirects=True,
                )
            except httpx.HTTPError:
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code == 200:
                if urlsplit(str(response.url)).netloc.lower() != host:
                    return None
                try:
                    data = response.json()
                except ValueError:
                    return None
                if not isinstance(data, dict):
                    return None
                rows = data.get("result")
                return len(rows) if isinstance(rows, list) else None
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            return None
    return _UNREACHABLE


async def _verify_ultipro(
    client: httpx.AsyncClient, board: Board, sem: asyncio.Semaphore
) -> int | None:
    """Live UKG Pro board: its own unauthenticated listing endpoint."""
    url = f"{board.url.rstrip('/')}/JobBoardView/LoadSearchResults"
    async with sem:
        await asyncio.sleep(0.05)
        for attempt in range(VERIFY_ATTEMPTS):
            try:
                response = await client.post(
                    url,
                    json={
                        "opportunitySearch": {
                            "Top": 1,
                            "Skip": 0,
                            "QueryString": "",
                            "OrderBy": [
                                {
                                    "Value": "date",
                                    "PropertyName": "PostedDate",
                                    "Ascending": False,
                                }
                            ],
                            "Filters": [],
                        }
                    },
                    headers={"content-type": "application/json"},
                    timeout=20.0,
                    follow_redirects=True,
                )
            except httpx.HTTPError:
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError:
                    return 0
                if isinstance(data, dict):
                    total = data.get("totalCount")
                    if isinstance(total, int):
                        return total
                    opportunities = data.get("opportunities")
                    return len(opportunities) if isinstance(opportunities, list) else 0
                return 0
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            return None
    return _UNREACHABLE


async def verify_board(
    client: httpx.AsyncClient, board: Board, sem: asyncio.Semaphore
) -> int | None:
    """Posting count for a live board, or ``None`` when it is not live."""
    if board.connector_id == "ats.workday":
        return await _verify_workday(client, board, sem)
    if board.connector_id == "ats.icims":
        return await _verify_icims(client, board, sem)
    if board.connector_id == "ats.ultipro":
        return await _verify_ultipro(client, board, sem)
    if board.connector_id == "ats.bamboohr":
        return await _verify_bamboohr(client, board, sem)
    template = API_TEMPLATES.get(board.connector_id)
    async with sem:
        await asyncio.sleep(0.05)
        if template:
            for attempt in range(VERIFY_ATTEMPTS):
                try:
                    response = await client.get(
                        template.format(tenant=board.tenant),
                        timeout=20.0,
                        follow_redirects=True,
                    )
                except httpx.HTTPError:
                    if attempt + 1 < VERIFY_ATTEMPTS:
                        await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                if response.status_code == 200:
                    try:
                        return _count_jobs(board.connector_id, response.json())
                    except ValueError:
                        return 0
                if response.status_code in (429, 500, 502, 503, 504):
                    if attempt + 1 < VERIFY_ATTEMPTS:
                        await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                return None
            return _UNREACHABLE
        # Unknown connector: a 200 on the board root still proves it exists.
        try:
            response = await client.get(board.url, timeout=15.0, follow_redirects=True)
        except httpx.HTTPError:
            return _UNREACHABLE
        return 0 if response.status_code == 200 else None


async def verify_all(
    client: httpx.AsyncClient, boards: dict[Board, int]
) -> tuple[list[tuple[Board, int]], dict[str, int]]:
    """Verify every candidate.

    Returns the ``(board, posting count)`` pairs that are live, plus one
    outcome per settled board (posting count, or ``-1`` for a board the ATS
    does not know) so the next run can skip it.
    """
    sem = asyncio.Semaphore(VERIFY_CONCURRENCY)
    verified: list[tuple[Board, int]] = []
    outcomes: dict[str, int] = {}
    live_by: dict[str, int] = defaultdict(int)
    dead_by: dict[str, int] = defaultdict(int)
    unreachable_by: dict[str, int] = defaultdict(int)
    done = 0
    started = time.monotonic()

    async def check(board: Board) -> None:
        nonlocal done
        count = await verify_board(client, board, sem)
        done += 1
        if count is None:
            outcomes[_cache_key(board)] = -1
            dead_by[board.connector_id] += 1
        elif count >= 0:
            verified.append((board, count))
            outcomes[_cache_key(board)] = count
            live_by[board.connector_id] += 1
        else:
            # Neither live nor known-dead: never remembered, always retried.
            unreachable_by[board.connector_id] += 1
        if done % 100 == 0 or done == len(boards):
            _log(
                "verify_progress",
                done=done,
                total=len(boards),
                live=len(verified),
                dead=sum(dead_by.values()),
                unreachable=sum(unreachable_by.values()),
                elapsed_s=round(time.monotonic() - started),
            )

    await asyncio.gather(*(check(board) for board in boards))
    _log(
        "verify_done",
        total=len(boards),
        live=len(verified),
        dead=sum(dead_by.values()),
        unreachable=sum(unreachable_by.values()),
        elapsed_s=round(time.monotonic() - started),
        live_by=dict(sorted(live_by.items())),
        dead_by=dict(sorted(dead_by.items())),
        unreachable_by=dict(sorted(unreachable_by.items())),
    )
    return verified, outcomes


# -------------------------------------------------------------------- output


def _seed_entry(board: Board, jobs: int) -> dict[str, object]:
    hint = _company_hint(board.tenant)
    if board.connector_id == "ats.ultipro" and any(char.isdigit() for char in board.tenant):
        # UKG Pro employers are addressed by opaque codes (``ACME1002``),
        # which are not readable company names.
        hint = None
    entry: dict[str, object] = {
        "url": board.url,
        "company_hint": hint,
        "ats_hint": board.connector_id,
        "confidence": 0.9 if jobs else 0.6,
    }
    if board.tenant_key:
        entry["tenant_key"] = board.tenant_key
    return entry


def _company_hint(tenant: str) -> str | None:
    """A readable label from a tenant slug, or ``None`` for opaque ids."""
    if _is_uuid(tenant):
        return None
    name = tenant
    # iCIMS tenants often read ``careers-acme``/``jobs-acme``.
    for prefix in ("careers-", "jobs-", "staff-"):
        if name.startswith(prefix) and len(name) > len(prefix):
            name = name[len(prefix) :]
            break
    return name.replace("-", " ").title()


def _is_uuid(value: str) -> bool:
    """Some Ashby boards are addressed by an opaque org id, not a name."""
    return bool(
        len(value) == 36 and value.count("-") == 4 and all(part for part in value.split("-"))
    )


def _shipped_rows() -> list[dict[str, object]]:
    """The seed file as it is right now, for merging a fresh ranking into it."""
    try:
        rows = json.loads(SEEDS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _has_postings(row: dict[str, object]) -> bool:
    confidence = row.get("confidence", 0.9)
    return (
        not isinstance(confidence, bool)
        and isinstance(confidence, (int, float))
        and confidence >= 0.9
    )


def _interleave(
    existing: list[dict[str, object]], new: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Place freshly verified rows in the shipped largest-first ranking.

    A previously written row no longer carries its posting count, so only the
    two tiers it was written in survive: boards with postings (confidence 0.9)
    belong above the first zero-posting row, and zero-posting boards go last.
    """
    split = next(
        (index for index, row in enumerate(existing) if not _has_postings(row)),
        len(existing),
    )
    with_postings = [row for row in new if _has_postings(row)]
    empty = [row for row in new if not _has_postings(row)]
    return existing[:split] + with_postings + existing[split:] + empty


def _write(
    verified: list[tuple[Board, int]],
    limit: int,
    merge_existing: bool = False,
) -> None:
    ranked = sorted(verified, key=lambda item: item[1], reverse=True)
    if limit > 0:
        ranked = ranked[:limit]
    seeds = [_seed_entry(board, jobs) for board, jobs in ranked]
    if merge_existing:
        seeds = _interleave(_shipped_rows(), seeds)
    SEEDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEDS_PATH.write_text(json.dumps(seeds, indent=2) + "\n", encoding="utf-8")
    by_connector: dict[str, int] = defaultdict(int)
    for board, _jobs in ranked:
        by_connector[board.connector_id] += 1
    _log(
        "write",
        path=str(SEEDS_PATH),
        rows=len(seeds),
        added=len(ranked),
        merged=merge_existing,
        by_connector=dict(sorted(by_connector.items())),
    )
    _log("top_boards", boards=[f"{board.url} ({jobs})" for board, jobs in ranked[:10]])


def _load_candidates() -> dict[Board, int]:
    """Candidates from the shipped seed file (used by ``--refresh``)."""
    try:
        seeds = json.loads(SEEDS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    known = (
        {connector for _host, connector in SEED_ATS_HOSTS}
        | set(SUBDOMAIN_ATS)
        | {"ats.ultipro"}
    )
    boards: dict[Board, int] = {}
    for seed in seeds if isinstance(seeds, list) else []:
        if not isinstance(seed, dict):
            continue
        url, connector_id = seed.get("url"), seed.get("ats_hint")
        if not isinstance(url, str) or not isinstance(connector_id, str):
            continue
        if connector_id not in known:
            continue
        if connector_id == "ats.ultipro":
            board = _ultipro_board(url)
            if board is not None:
                boards[board] = 1
            continue
        host = urlsplit(url).netloc.lower()
        tenant: str | None = None
        if connector_id in SUBDOMAIN_ATS:
            suffix = "." + SUBDOMAIN_ATS[connector_id]["domain"]
            if host.endswith(suffix):
                tenant = host[: -len(suffix)].split(".")[0]
        else:
            segments = [part for part in urlsplit(url).path.split("/") if part]
            tenant = segments[0] if segments else None
        if not tenant:
            continue
        tenant_key = seed.get("tenant_key")
        boards[
            Board(
                connector_id,
                tenant,
                url,
                tenant_key if isinstance(tenant_key, str) and tenant_key else None,
            )
        ] = 1
    return boards


def _cache_key(board: Board) -> str:
    """Stable identity of a board for the verified-outcome cache."""
    return "|".join(board.dedupe_key)


def _read_verified() -> dict[str, int]:
    try:
        raw = json.loads(VERIFIED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items() if isinstance(value, int)}


def _checkpoint_verified(outcomes: dict[str, int]) -> None:
    """Remember what this run settled so the next one can skip it."""
    if not outcomes:
        return
    stored = _read_verified()
    stored.update(outcomes)
    VERIFIED_PATH.write_text(json.dumps(stored, indent=1), encoding="utf-8")


def _by_connector(boards: dict[Board, int]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for board in boards:
        counts[board.connector_id] += 1
    return dict(sorted(counts.items()))


def _new_only(
    candidates: dict[Board, int],
    shipped: dict[Board, int],
    checked: dict[str, int] | None = None,
) -> dict[Board, int]:
    """The candidates the shipped seed does not already cover.

    Keyed on ``Board.dedupe_key``, not URL, so another locale or shard of a
    board already in the file is neither re-verified nor re-added.  A board a
    previous run settled as *dead* is skipped too; one it settled as live is
    not, because a live board missing from the file has to be put back.
    """
    present = {_cache_key(board) for board in shipped}
    dead = {key for key, jobs in (checked or {}).items() if jobs < 0}
    return {
        board: count
        for board, count in candidates.items()
        if _cache_key(board) not in present and _cache_key(board) not in dead
    }


def _merge(*sources: dict[Board, int]) -> dict[Board, int]:
    """Union of harvested boards, keeping the strongest URL evidence."""
    merged: dict[tuple[str, str], Board] = {}
    counts: dict[Board, int] = defaultdict(int)
    for source in sources:
        for board, count in source.items():
            key = board.dedupe_key
            current = merged.get(key)
            if current is None:
                merged[key] = board
                counts[board] += count
                continue
            counts[current] += count
    return dict(counts)


async def main() -> None:
    global LOG_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="keep at most N seeds (default: every live board)",
    )
    parser.add_argument("--resume", action="store_true", help="reuse the harvest caches")
    parser.add_argument(
        "--log-file",
        default=str(LOG_PATH),
        help="append the machine-readable run log here (default: .seed_build_log.jsonl)",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help=(
            "verify only candidates the shipped seed does not already cover, "
            "then merge them into it instead of re-verifying the whole file"
        ),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="skip harvest: re-verify and re-rank the shipped seed file",
    )
    parser.add_argument(
        "--skip-fixed",
        action="store_true",
        help=(
            "reuse the shipped fixed-host boards instead of re-walking the "
            "index for Greenhouse/Lever/Ashby/SmartRecruiters"
        ),
    )
    parser.add_argument(
        "--no-ct",
        action="store_true",
        help=(
            "skip certificate-transparency enumeration for iCIMS and walk its CDX domain instead"
        ),
    )
    parser.add_argument(
        "--workday-chunks",
        type=int,
        default=24,
        help="10,000-row CDX chunks to walk across Workday's shards (0 disables)",
    )
    parser.add_argument(
        "--icims-chunks",
        type=int,
        default=12,
        help="10,000-row CDX chunks to walk for iCIMS (0 disables)",
    )
    parser.add_argument(
        "--bamboohr-chunks",
        type=int,
        default=0,
        help="10,000-row CDX chunks to walk for BambooHR (0 disables)",
    )
    parser.add_argument(
        "--ultipro-chunks",
        type=int,
        default=12,
        help="10,000-row CDX chunks to walk for UKG Pro/UltiPro (0 disables)",
    )
    args = parser.parse_args()
    if args.refresh and args.incremental:
        parser.error("--refresh and --incremental are mutually exclusive")

    LOG_PATH = Path(args.log_file)
    checked: dict[str, int] = _read_verified() if args.incremental else {}
    mode = "refresh" if args.refresh else "incremental" if args.incremental else "harvest"
    _log("run_start", mode=mode, argv=" ".join(sys.argv[1:]))

    async with httpx.AsyncClient(
        headers={"User-Agent": "Lodestar seed builder"},
        follow_redirects=True,
    ) as client:
        if args.refresh:
            candidates = _load_candidates()
            _log(
                "refresh",
                boards=len(candidates),
                path=SEEDS_PATH.name,
                by_connector=_by_connector(candidates),
            )
        else:
            # Pre-flight only matters when CDX is actually used.  When the
            # caller only wants certificate transparency, skip it so a throttled
            # archive gateway cannot block the run.
            if (
                not args.skip_fixed
                or args.workday_chunks > 0
                or args.icims_chunks > 0
                or args.bamboohr_chunks > 0
                or args.ultipro_chunks > 0
            ):
                _log("preflight_start")
                reachable = 0
                healthy = 0
                for probe_attempt in range(5):
                    started = time.monotonic()
                    try:
                        probe = await client.get(
                            f"{CDX_ENDPOINT}?url=boards.greenhouse.io/a"
                            "&matchType=prefix&fl=original&collapse=urlkey"
                            "&limit=5&output=json",
                            timeout=30.0,
                        )
                    except httpx.HTTPError as exc:
                        _log(
                            "preflight_error",
                            attempt=probe_attempt + 1,
                            error=type(exc).__name__,
                            elapsed_s=round(time.monotonic() - started, 1),
                        )
                    else:
                        reachable += 1
                        healthy += probe.status_code == 200
                        _log(
                            "preflight_probe",
                            attempt=probe_attempt + 1,
                            status=probe.status_code,
                            bytes=len(probe.content),
                            elapsed_s=round(time.monotonic() - started, 1),
                        )
                    if healthy:
                        break
                    await asyncio.sleep(15.0)
                if not reachable:
                    _log("preflight_failed", reason="no HTTP response")
                    return
                if not healthy:
                    _log("preflight_throttled", note="harvest backs off per slice")
                else:
                    _log("preflight_ok")
            if args.skip_fixed:
                # The shipped file already holds every live fixed-host board
                # from the last full walk; re-walking it would spend an hour
                # reproducing the same candidates.
                _log("fixed_skipped", reason="--skip-fixed")
                fixed: dict[Board, int] = {}
            else:
                fixed = await harvest_all(client, args.resume)
            _log("fixed_done", candidates=len(fixed), by_connector=_by_connector(fixed))
            subdomains: dict[Board, int] = {}
            for connector_id, chunks in (
                ("ats.workday", args.workday_chunks),
                ("ats.icims", args.icims_chunks),
                ("ats.bamboohr", args.bamboohr_chunks),
            ):
                found: dict[Board, int] = {}
                if connector_id == "ats.icims" and not args.no_ct:
                    ct = await harvest_certificates(client, connector_id, args.resume)
                    if ct:
                        _log("ct_candidates", connector=connector_id, candidates=len(ct))
                        found.update(ct)
                if chunks > 0:
                    found.update(
                        await harvest_subdomains(client, connector_id, chunks, args.resume)
                    )
                    _log("subdomain_candidates", connector=connector_id, candidates=len(found))
                subdomains.update(found)
            if args.ultipro_chunks > 0:
                ultipro = await harvest_ultipro(client, args.ultipro_chunks, args.resume)
                _log("subdomain_candidates", connector="ats.ultipro", candidates=len(ultipro))
                subdomains.update(ultipro)
            shipped = _load_candidates()
            candidates = _merge(shipped, fixed, subdomains)
            _log(
                "candidates",
                total=len(candidates),
                shipped=len(shipped),
                by_connector=_by_connector(candidates),
            )
            if args.incremental:
                candidates = _new_only(candidates, shipped, checked)
                _log(
                    "incremental_filter",
                    candidates=len(candidates),
                    skipped=len(checked),
                    by_connector=_by_connector(candidates),
                )

        if not candidates:
            _log("no_candidates")
            return

        _log("verify_start", candidates=len(candidates), by_connector=_by_connector(candidates))
        verified, outcomes = await verify_all(client, candidates)
    _checkpoint_verified(outcomes)

    _write(verified, args.limit, merge_existing=args.incremental)
    _log("run_end", mode=mode, verified=len(verified))


if __name__ == "__main__":
    asyncio.run(main())
