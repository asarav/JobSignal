"""Build the bundled ATS seed list from the public web URL index.

Uses the Internet Archive CDX API directly with high per-query limits so each
bucket is captured in ONE request (not 50 paginated ones).  CDX can return
10,000 distinct URLs in ~13s.  Requests are spaced 5s apart to stay under the
gateway's rate limit.  Progress is checkpointed to ``.seed_build_cache.json``
after every slice, so an interrupted run resumes where it left off.

Run it (network required).  Expect ~30-60 min for a full sweep:

    python scripts/build_seed.py            # full harvest + verify
    python scripts/build_seed.py --limit 200 # smaller list
    python scripts/build_seed.py --resume    # skip harvest, use cache

The seed file is a *bootstrap*: discovery's engine is the web-index provider
(see ADR 0005).  The resulting list is the "one time" shipped artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode

import httpx
from app.plugins.discovery.web_index import DEFAULT_ATS_HOSTS, _tenant_of

ROOT = Path(__file__).resolve().parents[1]
SEEDS_PATH = ROOT / "app" / "plugins" / "discovery" / "seeds" / "ats_tenants.json"
CACHE_PATH = ROOT / ".seed_build_cache.json"

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
BUCKETS = "abcdefghijklmnopqrstuvwxyz0123456789"
CDX_LIMIT = 10000
REQUEST_GAP_S = 3.0
VERIFY_CONCURRENCY = min(32, max(8, (os.cpu_count() or 4) * 4))

API_TEMPLATES: dict[str, str] = {
    "ats.greenhouse": "https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs",
    "ats.lever": "https://api.lever.co/v0/postings/{tenant}?mode=json",
    "ats.ashby": "https://api.ashbyhq.com/posting-api/job-board/{tenant}",
    "ats.smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{tenant}/postings?limit=1",
}


async def harvest_slice(
    client: httpx.AsyncClient,
    host: str,
    connector_id: str,
    bucket: str,
    sem: asyncio.Semaphore,
) -> dict[str, int]:
    """Return {tenant: url_count} for one (host, bucket) slice."""
    counts: dict[str, int] = defaultdict(int)
    async with sem:
        await asyncio.sleep(REQUEST_GAP_S)
        params = {
            "url": f"{host}/{bucket}",
            "matchType": "prefix",
            "fl": "original",
            "collapse": "urlkey",
            "limit": str(CDX_LIMIT),
            "output": "json",
        }
        url = f"{CDX_ENDPOINT}?{urlencode(params)}"
        for attempt in range(2):
            try:
                r = await client.get(url, timeout=30.0)
            except httpx.HTTPError:
                await asyncio.sleep(5.0)
                continue
            if r.status_code == 200:
                try:
                    table = r.json()
                except ValueError:
                    return dict(counts)
                if isinstance(table, list):
                    for record in table[1:]:
                        if isinstance(record, list) and record and isinstance(record[0], str):
                            tenant = _tenant_of(record[0], host)
                            if tenant is not None:
                                counts[tenant] += 1
                return dict(counts)
            # 503/504/etc: retry with backoff
            await asyncio.sleep(5.0 * (attempt + 1))
        return dict(counts)


async def harvest_all(client: httpx.AsyncClient, resume: bool) -> dict[tuple[str, str], int]:
    completed: set[tuple[str, str]] = set()
    results: dict[tuple[str, str], int] = defaultdict(int)

    if CACHE_PATH.exists():
        try:
            raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "results" in raw:
                for k, v in raw.get("results", {}).items():
                    parts = k.split("|", 1)
                    if len(parts) == 2:
                        results[(parts[0], parts[1])] = int(v)
                for pair in raw.get("completed", []):
                    if isinstance(pair, (list, tuple)) and len(pair) == 2:
                        completed.add((str(pair[0]), str(pair[1])))
                print(
                    f"Resuming from cache: {len(completed)}/180 slices done, "
                    f"{len(results)} candidates so far.",
                    flush=True,
                )
                if resume:
                    return dict(results)
        except (OSError, ValueError, KeyError):
            completed = set()
            results = defaultdict(int)

    semaphore = asyncio.Semaphore(1)  # serial: one request at a time
    tasks = [
        (host, connector_id, bucket)
        for host, connector_id in DEFAULT_ATS_HOSTS
        for bucket in "abcdefghijklmnopqrstuvwxyz0123456789"
    ]
    todo = [t for t in tasks if (t[0], t[2]) not in completed]

    done = len(completed)
    total = len(tasks)
    started = time.monotonic()

    def checkpoint() -> None:
        CACHE_PATH.write_text(
            json.dumps(
                {
                    "completed": [[h, b] for (h, b) in completed],
                    "results": {f"{c}|{t}": n for (c, t), n in results.items()},
                },
                indent=1,
            ),
            encoding="utf-8",
        )

    async def run_one(host: str, connector_id: str, bucket: str) -> None:
        nonlocal done
        counts = await harvest_slice(client, host, connector_id, bucket, semaphore)
        for tenant, n in counts.items():
            results[(connector_id, tenant)] += n
        completed.add((host, bucket))
        done += 1
        if done % 5 == 0 or done == total:
            checkpoint()
            print(
                f"  harvested {done}/{total} slices "
                f"({len(results)} candidates, {round(time.monotonic()-started)}s)",
                flush=True,
            )

    await asyncio.gather(*(run_one(h, c, b) for h, c, b in todo), return_exceptions=True)
    checkpoint()
    return dict(results)


async def verify_board(
    client: httpx.AsyncClient,
    url: str,
    ats_hint: str | None,
    sem: asyncio.Semaphore,
) -> int | None:
    from urllib.parse import urlparse

    tenant = urlparse(url).path.strip("/").split("/")[0]
    template = {
        "ats.greenhouse": "https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs",
        "ats.lever": "https://api.lever.co/v0/postings/{tenant}?mode=json",
        "ats.ashby": "https://api.ashbyhq.com/posting-api/job-board/{tenant}",
        "ats.smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{tenant}/postings?limit=1",
    }.get(ats_hint)
    async with sem:
        await asyncio.sleep(0.05)
        if template:
            try:
                r = await client.get(
                    template.format(tenant=tenant),
                    timeout=20.0,
                    follow_redirects=True,
                )
                if r.status_code == 200:
                    data = r.json() if "json" in r.headers.get("content-type", "") else {}
                    if ats_hint == "ats.greenhouse":
                        return len(data.get("jobs", [])) if isinstance(data, dict) else 0
                    if ats_hint == "ats.lever":
                        return len(data) if isinstance(data, list) else 0
                    if ats_hint == "ats.ashby":
                        jb = data.get("jobBoard", {}) if isinstance(data, dict) else {}
                        return len(jb.get("jobPostings", [])) if isinstance(jb, dict) else 0
                    if ats_hint == "ats.smartrecruiters":
                        return data.get("totalFound", 0) if isinstance(data, dict) else 0
                    return 0
            except (httpx.HTTPError, ValueError):
                pass
        try:
            r = await client.get(url, timeout=15.0, follow_redirects=True)
            if r.status_code == 200:
                return 0
        except httpx.HTTPError:
            pass
        return None


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000, help="target seed count")
    parser.add_argument("--resume", action="store_true", help="skip harvest, use cache only")
    args = parser.parse_args()

    async with httpx.AsyncClient(
        headers={"User-Agent": "Lodestar seed builder"}, follow_redirects=True
    ) as client:
        # Pre-flight: verify CDX is reachable before starting a 30+ min harvest.
        print("Pre-flight: checking CDX availability...", flush=True)
        ok_count = 0
        for _ in range(3):
            try:
                r = await client.get(
                    "https://web.archive.org/cdx/search/cdx?url=boards.greenhouse.io/a&matchType=prefix&fl=original&collapse=urlkey&limit=5&output=json",
                    timeout=20.0,
                )
                if r.status_code == 200:
                    ok_count += 1
            except httpx.HTTPError:
                pass
            await asyncio.sleep(3.0)
        if ok_count < 2:
            print(
                    f"CDX is unreachable ({ok_count}/3 succeeded). "
                    "Aborting - try again later.",
                    flush=True,
                )
            return
        print(f"CDX OK ({ok_count}/3). Starting harvest...", flush=True)

        print("Harvesting candidates from CDX (high limit, serial, polite)...", flush=True)
        candidates = await harvest_all(client, args.resume)
        print(f"Harvested {len(candidates)} candidate tenants.", flush=True)

        # Verify + rank by job count.
        print(f"Verifying {len(candidates)} candidates against ATS APIs...", flush=True)
        sem = asyncio.Semaphore(VERIFY_CONCURRENCY)
        verified: list[dict] = []
        done = 0
        started = time.monotonic()

        async def check(item) -> None:
            nonlocal done
            (connector_id, tenant), evidence = item
            host = next(h for h, c in DEFAULT_ATS_HOSTS if c == connector_id)
            url = f"https://{host}/{tenant}"
            count = await verify_board(client, url, connector_id, sem)
            done += 1
            if count is not None:
                verified.append(
                    {
                        "url": url,
                        "company_hint": tenant.replace("-", " ").title(),
                        "ats_hint": connector_id,
                        "confidence": 0.9 if count > 0 else 0.6,
                        "_jobs": count,
                    }
                )
            if done % 50 == 0 or done == len(candidates):
                print(
                    f"  verified {done}/{len(candidates)} "
                    f"({len(verified)} live, {round(time.monotonic()-started)}s)",
                    flush=True,
                )

        await asyncio.gather(*(check(item) for item in candidates.items()))

    verified.sort(key=lambda s: s["_jobs"], reverse=True)
    top = verified[: args.limit]
    for s in top:
        del s["_jobs"]

    SEEDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEDS_PATH.write_text(json.dumps(top, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {len(top)} verified seeds to {SEEDS_PATH}", flush=True)
    print("Top 10 by open postings:")
    for s in verified[:10]:
        print(f"  {s['url']}  ({s['_jobs']} open)")


if __name__ == "__main__":
    asyncio.run(main())
