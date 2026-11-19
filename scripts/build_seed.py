"""Build the bundled ATS seed list from the public web URL index.

The seed file is a *bootstrap*: discovery's engine is the web-index provider
(see ADR 0005), and this script only refreshes the "one time" artifact that
gives a cold install something to crawl immediately.

How it works
------------

1. **Harvest.** Ask the Internet Archive CDX API for every
   ``(ATS host, leading-character bucket)`` slice, one request at a time with
   a short gap between requests.  Each request asks for ``limit=10000`` rows,
   which the gateway serves in roughly 13 s and which captures a whole bucket
   instead of a small sample.  Progress is checkpointed to
   ``.seed_build_cache.json`` after every few slices, so an interrupted run
   resumes where it left off.
2. **Verify.** Ask each candidate's public ATS JSON API whether the board is
   live (HTTP 200), and record how many postings it lists.  A board with zero
   postings is still live and stays in the list; a board the API does not know
   is dropped.
3. **Rank.** Write the live boards largest-first, so the cold-start warm-up
   (which registers at most ``discovery.max_new_sources_per_run`` per pass)
   reaches the biggest employers first.

Run it (network required):

    python scripts/build_seed.py                  # harvest, verify, rank
    python scripts/build_seed.py --limit 2000      # keep the top 2,000
    python scripts/build_seed.py --resume          # reuse the harvest cache
    python scripts/build_seed.py --refresh         # no harvest: re-verify and
                                                   # re-rank the shipped file

Coverage note: this enumerates the four ATS hosts whose boards live on a fixed
``host/{tenant}`` path.  Workday (``{tenant}.wdN.myworkdayjobs.com``) and iCIMS
(``{tenant}.icims.com``) put the tenant in the subdomain, so the same prefix
query cannot enumerate them; wiring those hosts into discovery is tracked in
§6.2.2 of the spec.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx
from app.plugins.discovery.web_index import DEFAULT_ATS_HOSTS, _tenant_of

ROOT = Path(__file__).resolve().parents[1]
SEEDS_PATH = ROOT / "app" / "plugins" / "discovery" / "seeds" / "ats_tenants.json"
CACHE_PATH = ROOT / ".seed_build_cache.json"

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
BUCKETS = "abcdefghijklmnopqrstuvwxyz0123456789"
CDX_LIMIT = 10000
REQUEST_GAP_S = 3.0
HARVEST_ATTEMPTS = 2
VERIFY_ATTEMPTS = 3
VERIFY_CONCURRENCY = min(32, max(8, (os.cpu_count() or 4) * 4))

#: Public per-tenant JSON APIs, one per supported connector.
API_TEMPLATES: dict[str, str] = {
    "ats.greenhouse": "https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs",
    "ats.lever": "https://api.lever.co/v0/postings/{tenant}?mode=json",
    "ats.ashby": "https://api.ashbyhq.com/posting-api/job-board/{tenant}",
    "ats.smartrecruiters": (
        "https://api.smartrecruiters.com/v1/companies/{tenant}/postings?limit=1"
    ),
}

Candidate = tuple[str, str]  # (connector id, tenant key)


def _host_for(connector_id: str) -> str | None:
    """The preferred indexed host for a connector (first in the rotation)."""
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


async def harvest_slice(
    client: httpx.AsyncClient,
    host: str,
    bucket: str,
    sem: asyncio.Semaphore,
) -> dict[str, int]:
    """Return ``{tenant: url_count}`` for one ``(host, bucket)`` slice."""
    counts: dict[str, int] = defaultdict(int)
    params = {
        "url": f"{host}/{bucket}",
        "matchType": "prefix",
        "fl": "original",
        "collapse": "urlkey",
        "limit": str(CDX_LIMIT),
        "output": "json",
    }
    url = f"{CDX_ENDPOINT}?{urlencode(params)}"
    async with sem:
        # One request at a time across slices, spaced out: the gateway
        # throttles bursts with 503s and connection resets.
        await asyncio.sleep(REQUEST_GAP_S)
        for attempt in range(HARVEST_ATTEMPTS):
            try:
                response = await client.get(url, timeout=60.0)
            except httpx.HTTPError as exc:
                print(
                    f"  cdx {host}/{bucket} attempt {attempt + 1} failed: {type(exc).__name__}",
                    flush=True,
                )
                if attempt + 1 < HARVEST_ATTEMPTS:
                    await asyncio.sleep(10.0 * (attempt + 1))
                continue
            if response.status_code == 200:
                try:
                    table = response.json()
                except ValueError:
                    print(f"  cdx {host}/{bucket} returned non-JSON", flush=True)
                    return dict(counts)
                if isinstance(table, list):
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
            print(
                f"  cdx {host}/{bucket} attempt {attempt + 1}: HTTP {response.status_code}",
                flush=True,
            )
            if attempt + 1 < HARVEST_ATTEMPTS:
                await asyncio.sleep(10.0 * (attempt + 1))
    return dict(counts)


async def harvest_all(client: httpx.AsyncClient, resume: bool) -> dict[Candidate, int]:
    """Harvest every slice, reusing the checkpointed results when present."""
    completed: set[tuple[str, str]] = set()
    results: dict[Candidate, int] = defaultdict(int)

    if CACHE_PATH.exists():
        try:
            raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "results" in raw:
                for key, value in raw.get("results", {}).items():
                    parts = key.split("|", 1)
                    if len(parts) == 2:
                        results[(parts[0], parts[1])] = int(value)
                for pair in raw.get("completed", []):
                    if isinstance(pair, (list, tuple)) and len(pair) == 2:
                        completed.add((str(pair[0]), str(pair[1])))
                print(
                    f"Resuming from cache: {len(completed)} slices done, "
                    f"{len(results)} candidates so far.",
                    flush=True,
                )
                if resume:
                    return dict(results)
        except (OSError, ValueError, KeyError):
            completed = set()
            results = defaultdict(int)

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
                        f"{connector}|{tenant}": count
                        for (connector, tenant), count in results.items()
                    },
                },
                indent=1,
            ),
            encoding="utf-8",
        )

    async def run_one(host: str, bucket: str) -> None:
        nonlocal done
        counts = await harvest_slice(client, host, bucket, semaphore)
        connector_id = next(connector for h, connector in DEFAULT_ATS_HOSTS if h == host)
        for tenant, count in counts.items():
            results[(connector_id, tenant)] += count
        completed.add((host, bucket))
        done += 1
        if done % 5 == 0 or done == total:
            checkpoint()
            print(
                f"  harvested {done}/{total} slices "
                f"({len(results)} candidates, {round(time.monotonic() - started)}s)",
                flush=True,
            )

    await asyncio.gather(*(run_one(host, bucket) for host, bucket in todo))
    checkpoint()
    return dict(results)


async def verify_board(
    client: httpx.AsyncClient,
    connector_id: str,
    tenant: str,
    sem: asyncio.Semaphore,
) -> int | None:
    """Posting count for a live board, or ``None`` when it is not live."""
    template = API_TEMPLATES.get(connector_id)
    async with sem:
        await asyncio.sleep(0.05)
        if template:
            for attempt in range(VERIFY_ATTEMPTS):
                try:
                    response = await client.get(
                        template.format(tenant=tenant),
                        timeout=20.0,
                        follow_redirects=True,
                    )
                except httpx.HTTPError:
                    if attempt + 1 < VERIFY_ATTEMPTS:
                        await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                if response.status_code == 200:
                    try:
                        return _count_jobs(connector_id, response.json())
                    except ValueError:
                        return 0
                if response.status_code in (429, 500, 502, 503, 504):
                    if attempt + 1 < VERIFY_ATTEMPTS:
                        await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                return None
        # Unknown connector: a 200 on the board root still proves it exists.
        try:
            response = await client.get(
                _board_url(connector_id, tenant), timeout=15.0, follow_redirects=True
            )
        except httpx.HTTPError:
            return None
        return 0 if response.status_code == 200 else None


async def verify_all(
    client: httpx.AsyncClient, candidates: dict[Candidate, int]
) -> list[tuple[Candidate, int]]:
    """Verify every candidate; returns ``(candidate, posting count)`` pairs."""
    sem = asyncio.Semaphore(VERIFY_CONCURRENCY)
    verified: list[tuple[Candidate, int]] = []
    done = 0
    started = time.monotonic()

    async def check(item: tuple[Candidate, int]) -> None:
        nonlocal done
        candidate, _evidence = item
        connector_id, tenant = candidate
        count = await verify_board(client, connector_id, tenant, sem)
        done += 1
        if count is not None:
            verified.append((candidate, count))
        if done % 100 == 0 or done == len(candidates):
            print(
                f"  verified {done}/{len(candidates)} "
                f"({len(verified)} live, {round(time.monotonic() - started)}s)",
                flush=True,
            )

    await asyncio.gather(*(check(item) for item in candidates.items()))
    return verified


def _seed_entry(connector_id: str, tenant: str, jobs: int) -> dict[str, object]:
    return {
        "url": _board_url(connector_id, tenant),
        "company_hint": None if _is_uuid(tenant) else tenant.replace("-", " ").title(),
        "ats_hint": connector_id,
        "confidence": 0.9 if jobs else 0.6,
    }


def _is_uuid(value: str) -> bool:
    """Some Ashby boards are addressed by an opaque org id, not a name."""
    return bool(
        len(value) == 36
        and value.count("-") == 4
        and all(part for part in value.split("-"))
    )


def _write(verified: list[tuple[Candidate, int]], limit: int) -> None:
    ranked = sorted(verified, key=lambda item: item[1], reverse=True)
    if limit > 0:
        ranked = ranked[:limit]
    seeds = [_seed_entry(*candidate, jobs) for candidate, jobs in ranked]
    SEEDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEDS_PATH.write_text(json.dumps(seeds, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {len(seeds)} verified seeds to {SEEDS_PATH}", flush=True)
    print("Top 10 by open postings:", flush=True)
    for candidate, jobs in ranked[:10]:
        print(f"  {_board_url(*candidate)}  ({jobs} open)", flush=True)


def _load_candidates() -> dict[Candidate, int]:
    """Candidates from the shipped seed file (used by ``--refresh``)."""
    try:
        seeds = json.loads(SEEDS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    candidates: dict[Candidate, int] = {}
    for seed in seeds if isinstance(seeds, list) else []:
        if not isinstance(seed, dict):
            continue
        url, connector_id = seed.get("url"), seed.get("ats_hint")
        if not isinstance(url, str) or not isinstance(connector_id, str):
            continue
        segments = [part for part in urlparse(url).path.split("/") if part]
        if segments and _host_for(connector_id):
            candidates[(connector_id, segments[0])] = 1
    return candidates


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="keep at most N seeds (default: every live board)",
    )
    parser.add_argument("--resume", action="store_true", help="reuse the harvest cache")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="skip harvest: re-verify and re-rank the shipped seed file",
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
            # Fail fast when the index is down instead of spending 10+ minutes
            # on timeouts.
            print("Pre-flight: checking CDX availability...", flush=True)
            ok_count = 0
            for _ in range(3):
                try:
                    probe = await client.get(
                        f"{CDX_ENDPOINT}?url=boards.greenhouse.io/a"
                        "&matchType=prefix&fl=original&collapse=urlkey"
                        "&limit=5&output=json",
                        timeout=20.0,
                    )
                    if probe.status_code == 200:
                        ok_count += 1
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(3.0)
            if ok_count < 2:
                print(
                    f"CDX is unreachable ({ok_count}/3 succeeded). Aborting - try again later.",
                    flush=True,
                )
                return
            print(f"CDX OK ({ok_count}/3). Starting harvest...", flush=True)
            # ``harvest_all`` keys results by (connector, tenant), so the two
            # Greenhouse host mirrors collapse onto one board here.
            candidates = await harvest_all(client, args.resume)
            print(f"Harvested {len(candidates)} candidate tenants.", flush=True)

        if not candidates:
            print("No candidates; nothing to verify.", flush=True)
            return

        print(f"Verifying {len(candidates)} candidates against ATS APIs...", flush=True)
        verified = await verify_all(client, candidates)

    _write(verified, args.limit)


if __name__ == "__main__":
    asyncio.run(main())
