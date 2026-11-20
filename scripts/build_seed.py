"""Build the bundled ATS seed list from the public web URL index.

The seed file is a *bootstrap*: discovery's engine is the web-index provider
(see ADR 0005), and this script only refreshes the "one time" artifact that
gives a cold install something to crawl immediately.

How it works
------------

1. **Harvest fixed-host tenants.** Ask the Internet Archive CDX API for every
   ``(ATS host, leading-character bucket)`` slice, one request at a time with
   a short gap between requests.  Each request asks for ``limit=10000`` rows,
   which the gateway serves in roughly 13 s and which captures a whole bucket
   instead of a small sample.
2. **Harvest subdomain tenants.** Workday and iCIMS put the employer in the
   subdomain, so a prefix query cannot reach them.  A ``matchType=domain``
   query walks the whole domain in 10,000-row chunks with CDX's resumption
   key, and a negative filter drops the marketing site (``www``) and asset
   paths that would otherwise fill the first chunks.
3. **Verify.** Ask each candidate's public API whether the board is live:
   Greenhouse/Lever/Ashby/SmartRecruiters expose per-tenant JSON, Workday
   exposes the CxS ``jobs`` endpoint its own site calls, and iCIMS exposes a
   search page.  A board with zero postings is still live and stays in the
   list; a board the API does not know is dropped.
4. **Rank.** Write the live boards largest-first, so the cold-start warm-up
   (which registers at most ``discovery.max_new_sources_per_run`` per pass)
   reaches the biggest employers first.

Progress is checkpointed after every slice and every subdomain chunk, so an
interrupted run resumes where it left off.  The archive gateway throttles
heavy scans with 503s; the harvester backs off for minutes rather than
abandoning a chunk.

Run it (network required):

    python scripts/build_seed.py                  # harvest, verify, rank
    python scripts/build_seed.py --limit 2000      # keep the top 2,000
    python scripts/build_seed.py --resume          # reuse the harvest cache
    python scripts/build_seed.py --refresh         # no harvest: re-verify and
                                                   # re-rank the shipped file
    python scripts/build_seed.py --skip-fixed      # only walk Workday/iCIMS,
                                                   # keeping the shipped boards
    python scripts/build_seed.py --workday-chunks 0 --icims-chunks 0
                                                   # fixed-host ATSs only
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
from pathlib import Path
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

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
CT_ENDPOINT = "https://crt.sh/"
CT_ATTEMPTS = 4
CT_BACKOFF_S = (20.0, 40.0, 80.0)
BUCKETS = "abcdefghijklmnopqrstuvwxyz0123456789"
CDX_LIMIT = 10000
REQUEST_GAP_S = 3.0
HARVEST_ATTEMPTS = 4
VERIFY_ATTEMPTS = 3
VERIFY_CONCURRENCY = min(16, max(4, (os.cpu_count() or 4) * 2))
#: Backoff before retrying a throttled archive chunk (seconds).
THROTTLE_BACKOFF_S = (30.0, 60.0, 120.0, 240.0)

#: Public per-tenant JSON APIs, one per fixed-host connector.
API_TEMPLATES: dict[str, str] = {
    "ats.greenhouse": "https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs",
    "ats.lever": "https://api.lever.co/v0/postings/{tenant}?mode=json",
    "ats.ashby": "https://api.ashbyhq.com/posting-api/job-board/{tenant}",
    "ats.smartrecruiters": (
        "https://api.smartrecruiters.com/v1/companies/{tenant}/postings?limit=1"
    ),
}

#: Hosts whose tenant is a subdomain; each is walked with a domain query.
SUBDOMAIN_ATS: dict[str, dict[str, str]] = {
    "ats.workday": {
        "domain": "myworkdayjobs.com",
        # Asset URLs (one tenant can own tens of thousands) crowd out boards.
        "filter": "!original:.*/assets/.*",
    },
    "ats.icims": {
        "domain": "icims.com",
        # www is iCIMS' own marketing site, not a customer board.
        "filter": "!original:.*://www[0-9]*\\.icims\\.com.*",
    },
}

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
TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
LOCALE_RE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2,4})?$")
WORKDAY_SHARD_RE = re.compile(r"^(?:impl-)?wd\d+$")


@dataclass(frozen=True, slots=True)
class Board:
    """One candidate board, independent of how it was harvested."""

    connector_id: str
    tenant: str
    url: str
    tenant_key: str | None = None

    @property
    def dedupe_key(self) -> tuple[str, str]:
        return (self.connector_id, self.tenant_key or self.tenant)


def _host_for(connector_id: str) -> str | None:
    """The preferred fixed host for a connector (first in the rotation)."""
    return next((host for host, conn in DEFAULT_ATS_HOSTS if conn == connector_id), None)


def _board_url(connector_id: str, tenant: str) -> str:
    host = _host_for(connector_id) or DEFAULT_ATS_HOSTS[0][0]
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
    return 0


# --------------------------------------------------------------- CDX plumbing


async def _cdx_get(client: httpx.AsyncClient, params: dict[str, str]) -> httpx.Response | None:
    """One CDX request with patient backoff while the gateway throttles."""
    query = f"{CDX_ENDPOINT}?{urlencode(params)}"
    for attempt in range(HARVEST_ATTEMPTS):
        try:
            response = await client.get(query, timeout=240.0)
        except httpx.HTTPError as exc:
            print(
                f"  cdx transport error (attempt {attempt + 1}): {type(exc).__name__}",
                flush=True,
            )
        else:
            if response.status_code == 200:
                return response
            print(
                f"  cdx HTTP {response.status_code} (attempt {attempt + 1})",
                flush=True,
            )
            if response.status_code not in (429, 500, 502, 503, 504):
                return None
        if attempt + 1 < HARVEST_ATTEMPTS:
            await asyncio.sleep(THROTTLE_BACKOFF_S[attempt])
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


async def harvest_slice(
    client: httpx.AsyncClient,
    host: str,
    bucket: str,
    sem: asyncio.Semaphore,
) -> dict[str, int]:
    """Return ``{tenant: url_count}`` for one ``(host, bucket)`` slice."""
    params = {
        "url": f"{host}/{bucket}",
        "matchType": "prefix",
        "fl": "original",
        "collapse": "urlkey",
        "limit": str(CDX_LIMIT),
        "output": "json",
    }
    async with sem:
        # One request at a time across slices, spaced out: the gateway
        # throttles bursts with 503s and connection resets.
        await asyncio.sleep(REQUEST_GAP_S)
        response = await _cdx_get(client, params)
    if response is None:
        return {}
    return _cdx_tenants(response.text, host)


async def harvest_all(client: httpx.AsyncClient, resume: bool) -> dict[Board, int]:
    """Harvest every fixed-host slice, reusing checkpointed results."""
    completed: set[tuple[str, str]] = set()
    boards: dict[Board, int] = defaultdict(int)

    if CACHE_PATH.exists():
        try:
            raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "results" in raw:
                for key, value in raw.get("results", {}).items():
                    parts = key.split("|", 1)
                    if len(parts) == 2:
                        connector_id, tenant = parts
                        boards[_board_for(connector_id, tenant)] += int(value)
                for pair in raw.get("completed", []):
                    if isinstance(pair, (list, tuple)) and len(pair) == 2:
                        completed.add((str(pair[0]), str(pair[1])))
                print(
                    f"Resuming from cache: {len(completed)} slices done, "
                    f"{len(boards)} candidates so far.",
                    flush=True,
                )
                if resume:
                    return dict(boards)
        except (OSError, ValueError, KeyError):
            completed = set()
            boards = defaultdict(int)

    tasks = [(host, bucket) for host, _connector_id in DEFAULT_ATS_HOSTS for bucket in BUCKETS]
    todo = [task for task in tasks if task not in completed]
    total = len(tasks)
    done = len(completed)
    started = time.monotonic()
    #: Serial: one index request at a time.  Bursts are what the gateway
    #: rejects; a paced single stream runs for as long as it needs to.
    semaphore = asyncio.Semaphore(1)

    def checkpoint() -> None:
        CACHE_PATH.write_text(
            json.dumps(
                {
                    "completed": [[host, bucket] for host, bucket in completed],
                    "results": {
                        f"{board.connector_id}|{board.tenant}": count
                        for board, count in boards.items()
                    },
                },
                indent=1,
            ),
            encoding="utf-8",
        )

    async def run_one(host: str, bucket: str) -> None:
        nonlocal done
        connector_id = next(connector for h, connector in DEFAULT_ATS_HOSTS if h == host)
        counts = await harvest_slice(client, host, bucket, semaphore)
        for tenant, count in counts.items():
            boards[_board_for(connector_id, tenant)] += count
        completed.add((host, bucket))
        done += 1
        if done % 5 == 0 or done == total:
            checkpoint()
            print(
                f"  harvested {done}/{total} slices "
                f"({len(boards)} candidates, {round(time.monotonic() - started)}s)",
                flush=True,
            )

    await asyncio.gather(*(run_one(host, bucket) for host, bucket in todo))
    checkpoint()
    return dict(boards)


def _board_for(connector_id: str, tenant: str) -> Board:
    return Board(connector_id, tenant, _board_url(connector_id, tenant))


# ---------------------------------------------------------- subdomain harvest


def _subdomain_board(connector_id: str, url: str) -> Board | None:
    """A board derived from one indexed URL, or ``None`` for site chrome."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
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
        if len(segments) < 2 or not LOCALE_RE.match(segments[0]):
            return None
        locale, site = segments[0], segments[1]
        return Board(
            connector_id,
            tenant,
            f"https://{host}/{locale}/{site}",
            tenant_key=tenant,
        )
    if connector_id == "ats.icims":
        if len(labels) != 1 or "." in sub:
            return None
        if sub in RESERVED_SUBDOMAINS or not TENANT_RE.match(sub):
            return None
        return Board(connector_id, sub, f"https://{host}")
    return None


def _chunk_urls(body: str) -> tuple[list[str], str | None]:
    """URLs in a CDX JSON chunk plus its resumption key, if any."""
    try:
        table = json.loads(body)
    except ValueError:
        return [], None
    if not isinstance(table, list):
        return [], None
    key: str | None = None
    if len(table) >= 2 and table[-2] == [] and isinstance(table[-1], list):
        key = table[-1][0] if table[-1] else None
        table = table[:-2]
    urls = [
        row[0] for row in table[1:] if isinstance(row, list) and row and isinstance(row[0], str)
    ]
    return urls, key


async def harvest_subdomains(
    client: httpx.AsyncClient,
    connector_id: str,
    chunks: int,
    resume: bool,
) -> dict[Board, int]:
    """Walk a subdomain-based ATS in resumable 10,000-row chunks."""
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
    for key, count in (state.get("boards") or {}).items():  # type: ignore[union-attr]
        parts = str(key).split("|", 3)
        if len(parts) == 4:
            board = Board(parts[0], parts[1], parts[2], parts[3] or None)
            boards[board] += int(count)
    resume_key = state.get("key") if isinstance(state.get("key"), str) else None
    done = int(state.get("chunks", 0) or 0)
    if resume and done and (not resume_key or done >= chunks):
        print(
            f"Resuming {connector_id}: {done} chunks cached, {len(boards)} candidates.",
            flush=True,
        )
        return dict(boards)

    started = time.monotonic()
    while done < chunks:
        params = {
            "url": cfg["domain"],
            "matchType": "domain",
            "fl": "original",
            "collapse": "urlkey",
            "limit": str(CDX_LIMIT),
            "output": "json",
            "showResumeKey": "true",
            "filter": cfg["filter"],
        }
        if resume_key:
            params["resumeKey"] = resume_key
        print(
            f"  {connector_id} chunk {done + 1}/{chunks} "
            f"(key={'set' if resume_key else 'start'})...",
            flush=True,
        )
        await asyncio.sleep(REQUEST_GAP_S)
        response = await _cdx_get(client, params)
        if response is None:
            print(f"  {connector_id}: giving up on this chunk", flush=True)
            break
        urls, resume_key = _chunk_urls(response.text)
        before = len(boards)
        for url in urls:
            board = _subdomain_board(connector_id, url)
            if board is not None:
                boards[board] += 1
        done += 1
        cache[connector_id] = {
            "chunks": done,
            "key": resume_key,
            "boards": {
                f"{b.connector_id}|{b.tenant}|{b.url}|{b.tenant_key or ''}": n
                for b, n in boards.items()
            },
        }
        SUBDOMAIN_CACHE_PATH.write_text(
            json.dumps(cache, indent=1),
            encoding="utf-8",
        )
        print(
            f"  {connector_id} chunk {done}/{chunks}: rows={len(urls)} "
            f"({len(boards)} candidates, +{len(boards) - before}, "
            f"{round(time.monotonic() - started)}s)",
            flush=True,
        )
        if not resume_key:
            print(f"  {connector_id}: domain exhausted", flush=True)
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
            try:
                response = await client.get(
                    CT_ENDPOINT,
                    params={"q": query, "output": "json", "exclude": "expired"},
                    timeout=240.0,
                )
            except httpx.HTTPError as exc:
                print(
                    f"  crt.sh transport error (attempt {attempt + 1}): {type(exc).__name__}",
                    flush=True,
                )
            else:
                if response.status_code == 200:
                    try:
                        data = response.json()
                    except ValueError:
                        data = None
                    if isinstance(data, list):
                        return data
                    print("  crt.sh returned a non-JSON body", flush=True)
                else:
                    print(
                        f"  crt.sh HTTP {response.status_code} (attempt {attempt + 1})",
                        flush=True,
                    )
                    if response.status_code not in (429, 500, 502, 503, 504):
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
        print(
            f"Using cached certificates for {connector_id}: {len(boards)} boards.",
            flush=True,
        )
        return boards

    hosts = {str(host) for host in (state.get("hosts") or []) if isinstance(host, str)}
    if not hosts:
        domain = SUBDOMAIN_ATS[connector_id]["domain"]
        entries: list[object] = []
        sem = asyncio.Semaphore(1)
        print(f"Querying certificate transparency for *.{domain}...", flush=True)
        entries = await _ct_entries(client, f"%.{domain}", sem)
        hosts = _ct_hosts(entries)
        print(f"  {len(hosts)} hostnames, {len(entries)} certificates.", flush=True)
        if not hosts:
            return {}
        cache[connector_id] = {"hosts": sorted(hosts)}
        CT_CACHE_PATH.write_text(json.dumps(cache, indent=1), encoding="utf-8")

    tenants: dict[str, str] = {}
    for host in sorted(hosts):
        tenant = _certificate_tenant(connector_id, host)
        if tenant is not None:
            tenants.setdefault(tenant, host)
    print(f"  {len(tenants)} {connector_id} tenants in the logs.", flush=True)

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


async def _verify_workday(
    client: httpx.AsyncClient, board: Board, sem: asyncio.Semaphore
) -> int | None:
    """Live Workday board: the CxS endpoint its own frontend calls."""
    parts = urlsplit(board.url)
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) < 2:
        return None
    site = segments[1]
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
    return None


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
    return None


async def verify_board(
    client: httpx.AsyncClient, board: Board, sem: asyncio.Semaphore
) -> int | None:
    """Posting count for a live board, or ``None`` when it is not live."""
    if board.connector_id == "ats.workday":
        return await _verify_workday(client, board, sem)
    if board.connector_id == "ats.icims":
        return await _verify_icims(client, board, sem)
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
        # Unknown connector: a 200 on the board root still proves it exists.
        try:
            response = await client.get(board.url, timeout=15.0, follow_redirects=True)
        except httpx.HTTPError:
            return None
        return 0 if response.status_code == 200 else None


async def verify_all(
    client: httpx.AsyncClient, boards: dict[Board, int]
) -> list[tuple[Board, int]]:
    """Verify every candidate; returns ``(board, posting count)`` pairs."""
    sem = asyncio.Semaphore(VERIFY_CONCURRENCY)
    verified: list[tuple[Board, int]] = []
    done = 0
    started = time.monotonic()

    async def check(board: Board) -> None:
        nonlocal done
        count = await verify_board(client, board, sem)
        done += 1
        if count is not None:
            verified.append((board, count))
        if done % 100 == 0 or done == len(boards):
            print(
                f"  verified {done}/{len(boards)} "
                f"({len(verified)} live, {round(time.monotonic() - started)}s)",
                flush=True,
            )

    await asyncio.gather(*(check(board) for board in boards))
    return verified


# -------------------------------------------------------------------- output


def _seed_entry(board: Board, jobs: int) -> dict[str, object]:
    entry: dict[str, object] = {
        "url": board.url,
        "company_hint": _company_hint(board.tenant),
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


def _write(verified: list[tuple[Board, int]], limit: int) -> None:
    ranked = sorted(verified, key=lambda item: item[1], reverse=True)
    if limit > 0:
        ranked = ranked[:limit]
    seeds = [_seed_entry(board, jobs) for board, jobs in ranked]
    SEEDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEDS_PATH.write_text(json.dumps(seeds, indent=2) + "\n", encoding="utf-8")
    by_connector: dict[str, int] = defaultdict(int)
    for board, _jobs in ranked:
        by_connector[board.connector_id] += 1
    print(f"\nWrote {len(seeds)} verified seeds to {SEEDS_PATH}", flush=True)
    print(f"By connector: {dict(sorted(by_connector.items()))}", flush=True)
    print("Top 10 by open postings:", flush=True)
    for board, jobs in ranked[:10]:
        print(f"  {board.url}  ({jobs} open)", flush=True)


def _load_candidates() -> dict[Board, int]:
    """Candidates from the shipped seed file (used by ``--refresh``)."""
    try:
        seeds = json.loads(SEEDS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    known = {connector for _host, connector in DEFAULT_ATS_HOSTS} | set(SUBDOMAIN_ATS)
    boards: dict[Board, int] = {}
    for seed in seeds if isinstance(seeds, list) else []:
        if not isinstance(seed, dict):
            continue
        url, connector_id = seed.get("url"), seed.get("ats_hint")
        if not isinstance(url, str) or not isinstance(connector_id, str):
            continue
        if connector_id not in known:
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="keep at most N seeds (default: every live board)",
    )
    parser.add_argument("--resume", action="store_true", help="reuse the harvest caches")
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
        help="10,000-row CDX chunks to walk for Workday (0 disables)",
    )
    parser.add_argument(
        "--icims-chunks",
        type=int,
        default=12,
        help="10,000-row CDX chunks to walk for iCIMS (0 disables)",
    )
    args = parser.parse_args()

    async with httpx.AsyncClient(
        headers={"User-Agent": "Lodestar seed builder"},
        follow_redirects=True,
    ) as client:
        if args.refresh:
            candidates = _load_candidates()
            print(
                f"Refreshing {len(candidates)} boards from {SEEDS_PATH.name}.",
                flush=True,
            )
        else:
            # Pre-flight only matters when CDX is actually used.  When the
            # caller only wants certificate transparency, skip it so a throttled
            # archive gateway cannot block the run.
            if not args.skip_fixed or args.workday_chunks > 0 or args.icims_chunks > 0:
                print("Pre-flight: checking CDX availability...", flush=True)
                reachable = 0
                healthy = 0
                for _ in range(5):
                    try:
                        probe = await client.get(
                            f"{CDX_ENDPOINT}?url=boards.greenhouse.io/a"
                            "&matchType=prefix&fl=original&collapse=urlkey"
                            "&limit=5&output=json",
                            timeout=30.0,
                        )
                    except httpx.HTTPError:
                        pass
                    else:
                        reachable += 1
                        healthy += probe.status_code == 200
                    if healthy:
                        break
                    await asyncio.sleep(15.0)
                if not reachable:
                    print(
                        "CDX is unreachable (no HTTP response). Aborting - try again later.",
                        flush=True,
                    )
                    return
                if not healthy:
                    print(
                        "CDX is throttling (503s); continuing, the harvest backs off.",
                        flush=True,
                    )
                else:
                    print("CDX OK. Starting harvest...", flush=True)
            if args.skip_fixed:
                # The shipped file already holds every live fixed-host board
                # from the last full walk; re-walking it would spend an hour
                # reproducing the same candidates.
                print("Skipping the fixed-host walk (--skip-fixed).", flush=True)
                fixed: dict[Board, int] = {}
            else:
                fixed = await harvest_all(client, args.resume)
            print(f"Fixed-host harvest: {len(fixed)} candidates.", flush=True)
            subdomains: dict[Board, int] = {}
            for connector_id, chunks in (
                ("ats.workday", args.workday_chunks),
                ("ats.icims", args.icims_chunks),
            ):
                found: dict[Board, int] = {}
                if connector_id == "ats.icims" and not args.no_ct:
                    ct = await harvest_certificates(client, connector_id, args.resume)
                    if ct:
                        print(
                            f"{connector_id}: {len(ct)} candidates from certificates.",
                            flush=True,
                        )
                        found.update(ct)
                if not found and chunks > 0:
                    found = await harvest_subdomains(client, connector_id, chunks, args.resume)
                    print(f"{connector_id}: {len(found)} candidates.", flush=True)
                subdomains.update(found)
            candidates = _merge(_load_candidates(), fixed, subdomains)
            print(f"Total candidates: {len(candidates)}.", flush=True)

        if not candidates:
            print("No candidates; nothing to verify.", flush=True)
            return

        print(f"Verifying {len(candidates)} candidates...", flush=True)
        verified = await verify_all(client, candidates)

    _write(verified, args.limit)


if __name__ == "__main__":
    asyncio.run(main())
