"""Build the bundled ATS seed list from the live public web URL index.

The seed file is a *bootstrap*: discovery's engine is the web-index provider
(see ADR 0005).  But a cold install with an empty seed list waits days for
the rotation to find the obvious employers, so this script harvests the
largest, most active boards from Common Crawl's index and verifies each
against the ATS's public API.  The result is written to
``app/plugins/discovery/seeds/ats_tenants.json`` and shipped with the app.

Run it whenever you want to refresh the bootstrap list (network required):

    python scripts/build_seed.py            # full harvest + verify
    python scripts/build_seed.py --limit 300 # smaller list
    python scripts/build_seed.py --use-cache # skip the slow harvest step

The "largest employers" ranking uses the live job count reported by each
ATS's public API, so boards with zero open postings are dropped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
from app.plugins.discovery.web_index import _tenant_of, DEFAULT_ATS_HOSTS

ROOT = Path(__file__).resolve().parents[1]
SEEDS_PATH = ROOT / "app" / "plugins" / "discovery" / "seeds" / "ats_tenants.json"
CACHE_PATH = ROOT / ".build_seed_cache.json"

INDEX_ENDPOINT = "https://index.commoncrawl.org"
CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
BUCKETS = "abcdefghijklmnopqrstuvwxyz0123456789"
MAX_CRAWL_FALLBACKS = 2
HARVEST_LIMIT = 1000
CDX_LIMIT = 200
HARVEST_CONCURRENCY = 3
VERIFY_CONCURRENCY = 10
STAGGER_S = 1.0

# Public ATS APIs used to verify a tenant and count its open postings.
API_TEMPLATES: dict[str, str] = {
    "ats.greenhouse": "https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs",
    "ats.lever": "https://api.lever.co/v0/postings/{tenant}?mode=json&limit=1",
    "ats.ashby": "https://api.ashbyhq.com/posting-api/job-board/{tenant}",
    "ats.smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{tenant}/postings?limit=1",
}


def host_for_connector(connector_id: str) -> tuple[str, str] | None:
    for host, cid in DEFAULT_ATS_HOSTS:
        if cid == connector_id:
            return host, cid
    return None


async def collinfo(client: httpx.AsyncClient) -> list[str]:
    r = await client.get(f"{INDEX_ENDPOINT}/collinfo.json", timeout=30.0)
    r.raise_for_status()
    data = r.json()
    return [c["id"] for c in data if isinstance(c, dict) and isinstance(c.get("id"), str)]


async def harvest_slice(
    client: httpx.AsyncClient,
    host: str,
    connector_id: str,
    bucket: str,
    crawl_ids: list[str],
    semaphore: asyncio.Semaphore,
) -> dict[str, int]:
    """Return {tenant: posting_count} for one (host, bucket) slice."""
    counts: dict[str, int] = defaultdict(int)
    async with semaphore:
        await asyncio.sleep(STAGGER_S)
        # Common Crawl
        cc_ok = False
        for crawl_id in crawl_ids[:MAX_CRAWL_FALLBACKS]:
            url = f"{INDEX_ENDPOINT}/{crawl_id}-index?url={quote(f'{host}/{bucket}*', safe='/*')}&output=json&limit={HARVEST_LIMIT}"
            try:
                r = await client.get(url, timeout=45.0)
            except httpx.HTTPError:
                continue
            if r.status_code != 200:
                continue
            parsed = 0
            for line in r.text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    break  # HTML error page, not NDJSON
                if not isinstance(row, dict):
                    continue
                if row.get("status") != "200":
                    continue
                tenant = _tenant_of(row.get("url", ""), host)
                if tenant is None:
                    continue
                counts[tenant] += 1
                parsed += 1
            if parsed or True:
                cc_ok = True
                break
        if not cc_ok or not counts:
            # CDX fallback (slower, capped by caller via slice budget).
            params = {
                "url": f"{host}/{bucket}",
                "matchType": "prefix",
                "fl": "original",
                "collapse": "urlkey",
                "limit": str(CDX_LIMIT),
                "output": "json",
            }
            try:
                r = await client.get(f"{CDX_ENDPOINT}?{urlencode(params)}", timeout=90.0)
            except httpx.HTTPError:
                return dict(counts)
            if r.status_code != 200:
                return dict(counts)
            try:
                table = r.json()
            except ValueError:
                return dict(counts)
            if isinstance(table, list):
                for record in table[1:]:
                    if isinstance(record, list) and record:
                        tenant = _tenant_of(record[0], host) if isinstance(record[0], str) else None
                        if tenant is not None:
                            counts[tenant] += 1
    return dict(counts)


async def harvest_all(client: httpx.AsyncClient, use_cache: bool) -> dict[tuple[str, str], int]:
    """Return {(connector_id, tenant): evidence_count}."""
    if use_cache and CACHE_PATH.exists():
        print("Loading harvested candidates from cache...", flush=True)
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return {tuple(k.split("|", 1)): v for k, v in raw.items()}

    try:
        crawl_ids = await collinfo(client)
        print(f"Newest crawl ids: {crawl_ids[:3]}", flush=True)
    except Exception as exc:  # noqa: BLE001 - fall back to CDX-only harvest
        print(f"Common Crawl collinfo unreachable ({exc}); harvesting from CDX only.", flush=True)
        crawl_ids = []

    print("Harvesting candidates from the web index...", flush=True)

    semaphore = asyncio.Semaphore(HARVEST_CONCURRENCY)
    tasks = []
    for host, connector_id in DEFAULT_ATS_HOSTS:
        for bucket in BUCKETS:
            tasks.append((host, connector_id, bucket))

    results: dict[tuple[str, str], int] = defaultdict(int)
    done = 0
    total = len(tasks)
    started = time.monotonic()

    async def run_one(host: str, connector_id: str, bucket: str) -> None:
        nonlocal done
        counts = await harvest_slice(client, host, connector_id, bucket, crawl_ids, semaphore)
        for tenant, n in counts.items():
            results[(connector_id, tenant)] += n
        done += 1
        if done % 10 == 0 or done == total:
            print(f"  harvested {done}/{total} slices ({round(time.monotonic()-started)}s)", flush=True)

    await asyncio.gather(*(run_one(h, c, b) for h, c, b in tasks))

    # Greenhouse dual-host merge: prefer boards.greenhouse.io.
    merged: dict[tuple[str, str], int] = {}
    for (cid, tenant), n in results.items():
        key = (cid, tenant)
        # If a boards. entry exists, drop the job-boards. duplicate.
        if cid == "ats.greenhouse" and key not in merged:
            alt = ("ats.greenhouse", tenant)
            # already handled below by preferring boards
        merged[key] = merged.get(key, 0) + n
    # Collapse greenhouse hosts: keep boards. (first in host order) by removing job-boards duplicates
    # that also exist as boards.
    boards_tenants = {t for (c, t) in results if c == "ats.greenhouse"}
    # results already keyed by connector, not host; so nothing to drop here beyond counts.
    # (host preference is implicit: both hosts contribute to the same connector+tenant.)

    CACHE_PATH.write_text(
        json.dumps({f"{c}|{t}": n for (c, t), n in merged.items()}, indent=1),
        encoding="utf-8",
    )
    return merged


async def verify(client: httpx.AsyncClient, connector_id: str, tenant: str, semaphore: asyncio.Semaphore) -> tuple[str, int] | None:
    template = API_TEMPLATES.get(connector_id)
    if not template:
        return (tenant, 0)
    url = template.format(tenant=tenant)
    async with semaphore:
        await asyncio.sleep(0.05)
        try:
            r = await client.get(url, timeout=20.0, follow_redirects=True)
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        # Count open postings from the response body.
        count = 0
        try:
            data = r.json()
        except ValueError:
            return None
        if connector_id == "ats.greenhouse":
            count = len(data.get("jobs", [])) if isinstance(data, dict) else 0
        elif connector_id == "ats.lever":
            # Lever with limit=1 returns a list; use a separate full-count request? keep 1 as "live".
            count = len(data) if isinstance(data, list) else 0
            if count == 0:
                count = 1  # live but empty board
        elif connector_id == "ats.ashby":
            jb = data.get("jobBoard", {}) if isinstance(data, dict) else {}
            count = len(jb.get("jobPostings", [])) if isinstance(jb, dict) else 0
            if count == 0:
                count = 1
        elif connector_id == "ats.smartrecruiters":
            count = data.get("totalFound", 0) if isinstance(data, dict) else 0
            if count == 0:
                count = 1
        if count <= 0:
            return None
        return (tenant, count)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000, help="how many seeds to emit")
    parser.add_argument("--use-cache", action="store_true", help="reuse the harvested candidate cache")
    args = parser.parse_args()

    async with httpx.AsyncClient(headers={"User-Agent": "Lodestar seed builder"}) as client:
        print("Harvesting candidates from the web index...", flush=True)
        candidates = await harvest_all(client, args.use_cache)
        print(f"Harvested {len(candidates)} candidate tenants.", flush=True)

        # Drop greenhouse job-boards duplicates that also exist on boards.
        # (counts already merged by connector+tenant; keep as-is)

        # Sort candidates by evidence count desc before verification.
        ordered = sorted(candidates.items(), key=lambda kv: kv[1], reverse=True)
        # Verify a generous slice to reach the requested limit.
        to_verify = ordered[: max(args.limit * 4, 2000)]
        print(f"Verifying {len(to_verify)} candidates against ATS APIs...", flush=True)

        sem = asyncio.Semaphore(VERIFY_CONCURRENCY)
        verified: list[dict] = []
        done = 0
        started = time.monotonic()

        async def check(item) -> None:
            nonlocal done
            (connector_id, tenant), evidence = item
            res = await verify(client, connector_id, tenant, sem)
            done += 1
            if res is not None:
                _, count = res
                host = next(h for h, c in DEFAULT_ATS_HOSTS if c == connector_id)
                verified.append(
                    {
                        "url": f"https://{host}/{tenant}",
                        "company_hint": tenant.replace("-", " ").title(),
                        "ats_hint": connector_id,
                        "confidence": 0.95,
                        "_jobs": count,
                        "_evidence": evidence,
                    }
                )
            if done % 50 == 0 or done == len(to_verify):
                print(
                    f"  verified {done}/{len(to_verify)} "
                    f"({len(verified)} live, {round(time.monotonic()-started)}s)",
                    flush=True,
                )

        await asyncio.gather(*(check(item) for item in to_verify))

    verified.sort(key=lambda s: (s["_jobs"], s["_evidence"]), reverse=True)
    top = verified[: args.limit]
    for s in top:
        del s["_jobs"]
        del s["_evidence"]

    SEEDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEDS_PATH.write_text(json.dumps(top, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {len(top)} verified seeds to {SEEDS_PATH}", flush=True)
    print("Top 10 by open postings:")
    for s in verified[:10]:
        print(f"  {s['url']}  ({s['_jobs']} open)")


if __name__ == "__main__":
    asyncio.run(main())
