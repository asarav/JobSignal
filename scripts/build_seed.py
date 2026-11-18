"""Build the bundled ATS seed list incrementally, as discovery happens.

Unlike a one-shot batch harvest, this script runs discovery passes one at a
time (the same paced slices the app uses), collects every newly discovered
board, verifies it is live via the ATS public API, records its open-posting
count, and appends it to ``app/plugins/discovery/seeds/ats_tenants.json``
immediately.  Seeds are sorted by open-posting count so the largest
employers come first.  Progress is checkpointed to ``.seed_build_state.json``
after every pass, so an interrupted run resumes where it left off.

Boards are verified here (ATS API liveness + job count) so the shipped seed
file only contains live boards and is ordered by employer size.  Per spec 6.4,
the crawler still does the real verification + extraction at crawl time.

Run it (network required).  It stops when the seed list reaches ``--limit``
(default 1000) or 5 consecutive passes find nothing new:

    python scripts/build_seed.py            # run until target (background)
    python scripts/build_seed.py --limit 200 # smaller target
    python scripts/build_seed.py --passes 1  # one pass only (for testing)

The seed file is a *bootstrap*: discovery's engine is the web-index provider
(see ADR 0005).  The resulting list is the "one time" shipped artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.discovery import DiscoveryQuery
from app.infrastructure.http.httpx_client import HttpxClient
from app.application.config import AppConfig
from app.plugins.discovery.web_index import WebIndexProvider

ROOT = Path(__file__).resolve().parents[1]
SEEDS_PATH = ROOT / "app" / "plugins" / "discovery" / "seeds" / "ats_tenants.json"
STATE_PATH = ROOT / ".seed_build_state.json"

SLICES_PER_PASS = 24
PASS_TIMEOUT_S = 300.0
HARVEST_LIMIT = 10000
VERIFY_CONCURRENCY = min(32, max(8, (os.cpu_count() or 4) * 4))

API_TEMPLATES: dict[str, str] = {
    "ats.greenhouse": "https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs",
    "ats.lever": "https://api.lever.co/v0/postings/{tenant}?mode=json",
    "ats.ashby": "https://api.ashbyhq.com/posting-api/job-board/{tenant}",
    "ats.smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{tenant}/postings?limit=1",
}


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    existing = []
    if SEEDS_PATH.exists():
        try:
            for e in json.loads(SEEDS_PATH.read_text(encoding="utf-8")):
                if isinstance(e, dict) and e.get("url"):
                    e = dict(e)
                    e.setdefault("_jobs", 0)
                    e.setdefault("_verified", True)
                    existing.append(e)
        except (OSError, json.JSONDecodeError):
            pass
    return {"cursor": 0, "seeds": existing, "stall": 0}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    # Sort by open-posting count desc so largest employers come first.
    ordered = sorted(state["seeds"], key=lambda e: e.get("_jobs", 0), reverse=True)
    out = []
    for e in ordered:
        out.append(
            {
                "url": e["url"],
                "company_hint": e.get("company_hint"),
                "ats_hint": e.get("ats_hint"),
                "confidence": e.get("confidence", 0.95),
            }
        )
    SEEDS_PATH.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")


async def verify_board(client: httpx.AsyncClient, url: str, ats_hint: str | None, sem: asyncio.Semaphore) -> int | None:
    """Returns open-posting count if live, None if dead/unreachable."""
    from urllib.parse import urlparse

    tenant = urlparse(url).path.strip("/").split("/")[0]
    template = API_TEMPLATES.get(ats_hint) if ats_hint else None
    async with sem:
        await asyncio.sleep(0.05)
        if template:
            try:
                r = await client.get(template.format(tenant=tenant), timeout=20.0, follow_redirects=True)
                if r.status_code == 200:
                    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
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
        # Fallback: board root.
        try:
            r = await client.get(url, timeout=15.0, follow_redirects=True)
            if r.status_code == 200:
                return 0  # live but count unknown
        except httpx.HTTPError:
            pass
        return None


async def run_pass(state: dict, ctx_factory) -> int:
    cursor = state["cursor"]
    provider = WebIndexProvider(limit=HARVEST_LIMIT)
    existing_urls = {e["url"] for e in state["seeds"]}
    new_candidates: list = []
    try:
        query = DiscoveryQuery(extra={"cursor": cursor, "slices": SLICES_PER_PASS})
        ctx = ctx_factory()
        async for src in provider.discover(query, ctx):
            if src.url not in existing_urls:
                new_candidates.append(src)
                existing_urls.add(src.url)
    except Exception as exc:  # noqa: BLE001
        print(f"  pass at cursor {cursor} failed: {exc}; cursor held.", flush=True)
        return 0
    if not new_candidates:
        print(f"  pass at cursor {cursor}: no new candidates.", flush=True)
        state["cursor"] = cursor + SLICES_PER_PASS
        return 0

    sem = asyncio.Semaphore(VERIFY_CONCURRENCY)
    async with httpx.AsyncClient(headers={"User-Agent": "Lodestar seed builder"}, follow_redirects=True) as client:
        async def check(src) -> tuple:
            count = await verify_board(client, src.url, src.ats_hint, sem)
            return src, count

        results = await asyncio.gather(*(check(s) for s in new_candidates))

    added = 0
    for src, count in results:
        if count is None:
            continue  # dead / unreachable
        state["seeds"].append(
            {
                "url": src.url,
                "company_hint": src.company_hint,
                "ats_hint": src.ats_hint,
                "confidence": src.confidence if src.confidence else 0.95,
                "_jobs": count,
                "_verified": True,
            }
        )
        added += 1
    print(
        f"  pass at cursor {cursor}: {len(new_candidates)} candidates, "
        f"{added} live (+{added}, total {len(state['seeds'])}).",
        flush=True,
    )
    state["cursor"] = cursor + SLICES_PER_PASS
    return added


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000, help="target seed count")
    parser.add_argument("--passes", type=int, default=0, help="max passes (0 = until target or stall)")
    args = parser.parse_args()

    state = load_state()
    # Verify any legacy (unverified) seeds.
    unverified = [e for e in state["seeds"] if not e.get("_verified", False) or "_jobs" not in e]
    if unverified:
        print(f"Verifying {len(unverified)} existing seeds...", flush=True)
        sem = asyncio.Semaphore(VERIFY_CONCURRENCY)
        async with httpx.AsyncClient(headers={"User-Agent": "Lodestar seed builder"}, follow_redirects=True) as client:
            async def check(e: dict) -> bool:
                count = await verify_board(client, e["url"], e.get("ats_hint"), sem)
                if count is None:
                    return False
                e["_jobs"] = count
                e["_verified"] = True
                return True

            results = await asyncio.gather(*(check(e) for e in unverified))
        before = len(state["seeds"])
        state["seeds"] = [e for e in state["seeds"] if e.get("_verified", False)]
        print(f"  kept {len(state['seeds'])}/{before} live boards.", flush=True)
        save_state(state)

    print(
        f"Starting: {len(state['seeds'])} seeds already, cursor {state['cursor']}, "
        f"target {args.limit}.",
        flush=True,
    )

    http = HttpxClient(AppConfig().http)
    ctx_factory = lambda: ExecutionContext(  # noqa: E731
        task_id="seed-build",
        clock=None,  # type: ignore[arg-type]
        http=http,
        logger=_NullLogger(),
        cancel=CancellationToken(),
        deadline=None,
        config={},
    )

    max_passes = args.passes if args.passes > 0 else 10_000
    for i in range(max_passes):
        if len(state["seeds"]) >= args.limit:
            print(f"Reached target ({len(state['seeds'])} >= {args.limit}). Done.", flush=True)
            break
        print(f"\n--- pass {i + 1}: cursor {state['cursor']}, seeds {len(state['seeds'])} ---", flush=True)
        try:
            added = await asyncio.wait_for(run_pass(state, ctx_factory), timeout=PASS_TIMEOUT_S)
        except asyncio.TimeoutError:
            print("  pass timed out; cursor held.", flush=True)
            state["stall"] = state.get("stall", 0) + 1
            save_state(state)
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"  pass error: {exc}", flush=True)
            state["stall"] = state.get("stall", 0) + 1
            save_state(state)
            continue

        if added == 0:
            state["stall"] = state.get("stall", 0) + 1
        else:
            state["stall"] = 0
        save_state(state)
        if state.get("stall", 0) >= 5:
            print("5 consecutive passes added nothing; stopping.", flush=True)
            break

    print(f"\nFinal: {len(state['seeds'])} seeds. Seed file: {SEEDS_PATH}", flush=True)


class _NullLogger:
    def warning(self, *a, **k): pass
    def info(self, *a, **k): pass
    def error(self, *a, **k): pass


if __name__ == "__main__":
    asyncio.run(main())
