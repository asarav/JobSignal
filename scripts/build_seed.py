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

Progress is checkpointed after every slice and every subdomain chunk, and the
verification cache is written every 1,000 settled boards (not only at the end),
so an interrupted run — hours in — resumes where it left off instead of
re-verifying the archive backlog.  Every checkpoint goes to a temp file and is
renamed into place, so a kill mid-write leaves the previous complete file
rather than a truncated one.  With ``--incremental`` every settled board is
remembered (``.seed_build_verified.json``), so a later expansion pass verifies
only boards no earlier pass has seen; live results settled in the last
``RESUME_LIVE_DAYS`` are written without being probed again.  The archive
gateway throttles heavy
scans with 503s; the harvester backs off for minutes rather than abandoning a
chunk.

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
from collections.abc import Callable, Mapping, Sequence
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

from app.domain.services.board_identity import board_identity  # noqa: E402
from app.plugins.ats.icims import _job_links  # noqa: E402
from app.plugins.ats.jazzhr import _DETAIL_RE as JAZZHR_DETAIL_RE  # noqa: E402
from app.plugins.ats.paylocity import _DETAIL_RE as PAYLOCITY_DETAIL_RE  # noqa: E402
from app.plugins.discovery.seed_shards import (  # noqa: E402
    SeedRecord,
    read_all_records,
    records_from_legacy_rows,
    write_seed_shards,
)
from app.plugins.discovery.web_index import DEFAULT_ATS_HOSTS, _tenant_of  # noqa: E402

SEEDS_DIR = ROOT / "app" / "plugins" / "discovery" / "seeds"
#: The legacy single-file layout, still read when the shards are absent.
SEEDS_PATH = SEEDS_DIR / "ats_tenants.json"

#: How long a board settled as dead stays out of a new verification run.  A
#: retired tenant can come back, and the old cache remembered ``-1`` forever.
RECHECK_DEAD_DAYS = 90

#: Connectors whose public listing is rendered client-side (iframe/JS), so a
#: verified zero there means "no postings observed", not "no postings".  Only
#: a confident zero is allowed to demote a board into the empty tier.
ZERO_UNCONFIDENT = frozenset({"ats.icims", "ats.taleo", "ats.workday"})
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
#: How many verifications are in flight per launch; the time budget is
#: re-checked between batches.
VERIFY_BATCH = 256

#: Settled verifications between checkpoint writes.  Verifying tens of
#: thousands of boards takes hours; losing at most this many on an
#: interruption costs seconds, while writing after every board would spend the
#: run rewriting the cache.
VERIFY_CHECKPOINT_EVERY = 1_000

#: How long a settled *live* verification may be reused on `--incremental`.
#: This is what makes an interrupted run resumable without re-checking boards
#: it already verified: within this window the settled result is written to the
#: shards as if this run had verified it; older entries are checked again so a
#: stale cache cannot pin an old posting count forever.
RESUME_LIVE_DAYS = 7

#: Measured per-host request pacing.  Paylocity answers 20/20 at one request a
#: second, 5/20 at two and 0/20 at four, with a ``Retry-After`` on every 429 --
#: the budget is per client IP, so concurrency cannot buy anything and every
#: extra request just spends a candidate on a refusal instead.
HOST_PACE_S: dict[str, float] = {
    "recruiting.paylocity.com": 1.05,
    # Measured: Workable's widget API refuses a burst (429 to 20 requests at
    # 0.6 s apart) and a sweep at seven requests a second blocks the caller
    # outright -- the whole pool then answers 429 with no ``Retry-After``, and
    # the block outlives an hour of waiting.  Pacing cannot rescue a caller
    # that is already blocked, so a dead-pool reclaim waits for the block to
    # clear and then keeps to this rate.
    "apply.workable.com": 1.0,
}
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
#: Hard wall-clock cap on one index request.  ``httpx``'s read timeout only
#: fires between socket reads, so a gateway that trickles keepalive bytes holds
#: a request open past every per-read timeout: one such request sat with no log
#: line for 41 minutes and stalled a whole bucket sweep.  This cap makes every
#: request either answer or fail inside a bounded window.
CDX_DEADLINE_S = 300.0
#: Pause between tenant buckets that neither shape answered.  The gateway
#: throttles these heavy scans for long stretches, so a sweep that retries
#: back-to-back only spends its budget inside the same bad window; spacing the
#: probes out lets a later bucket catch the moment it starts serving again.
BUCKET_PAUSE_S = 60.0
#: Default wall clock a bucket sweep may spend before it stops and writes what
#: it has.  An unanswered bucket stays checkpointed for the next run.
BUCKET_BUDGET_S = 1800.0
#: Longest tenant prefix a bucket may be partitioned into: ``a`` falls back to
#: ``aa``..``a9``, and those to three characters, when the index refuses it.
BUCKET_SPLIT_DEPTH = 3

#: Oracle Fusion HCM pods that host Candidate Experience sites.  Oracle is
#: enumerated per pod because the whole ``oraclecloud.com`` domain is far too
#: large for the archive gateway, while one pod's domain query answers in
#: 17-38 s (measured).  The discovery provider rotates the same list.
ORACLE_PODS: tuple[str, ...] = (
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
#: ``/hcmUI/CandidateExperience/{lang}/sites/{site}``; the site code is what
#: makes the board, and the locale is a mirror of it.
ORACLE_PATH_RE = re.compile(
    r"^/hcmUI/CandidateExperience(?:/(?P<lang>[a-z]{2}(?:-[A-Za-z]{2,4})?))?"
    r"/sites/(?P<site>[A-Za-z0-9_]+)(?:/|$)",
    re.IGNORECASE,
)
#: A Taleo career section: ``{tenant}.taleo.net/careersection/{section}/...``.
TALEO_PATH_RE = re.compile(r"^/careersection/(?P<section>[A-Za-z0-9_]+)(?:/|$)", re.IGNORECASE)
#: Posting links on a Taleo search page (the retired server-rendered shape).
TALEO_DETAIL_RE = re.compile(r"jobdetail\.ftl\?job=([A-Za-z0-9_-]+)", re.IGNORECASE)
#: Taleo's current career sections render their listing client-side and post
#: the query to this endpoint, so the page's HTML holds no posting links at all
#: (measured: a live section with 88 postings links zero ``jobdetail.ftl``
#: pages).  The section's portal number is in the page it serves.
TALEO_PORTAL_RE = re.compile(r"portal=(\d+)")
TALEO_SEARCH_PATH = "/careersection/rest/jobboard/searchjobs?lang=en&portal={portal}"
#: Eightfold's board page names the employer web domain its API expects.
EIGHTFOLD_DOMAIN_RE = re.compile(r'"domain"\s*:\s*"([A-Za-z0-9.-]+)"')

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
    "ats.rippling": "https://ats.rippling.com/api/v2/board/{tenant}/jobs",
}

#: Fixed hosts the seed builder walks.  Workable is path-addressed like the
#: others but is not part of the runtime rotation, so it is added here
#: instead of to ``web_index.DEFAULT_ATS_HOSTS``.
SEED_ATS_HOSTS: tuple[tuple[str, str], ...] = (
    *DEFAULT_ATS_HOSTS,
    ("apply.workable.com", "ats.workable"),
    ("ats.rippling.com", "ats.rippling"),
)


#: Hosts whose tenant is a subdomain; each is walked with a domain query.
#: ``filters`` keep board roots only: a tenant can own tens of thousands of
#: asset, job-detail and API URLs, and every one of them used to crowd a chunk
#: that could have named a hundred new employers instead.
class _PathCfg(TypedDict):
    """A path-addressed ATS: one host, the board's identity inside the path."""

    domain: str
    filter: str
    parse: Callable[[str], Board | None]


class _PrefixCfg(TypedDict):
    """A path-sliced ATS: one host, every board under a known URL prefix."""

    slices: tuple[str, ...]
    parse: Callable[[str], Board | None]


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
    "wd1",
    "wd2",
    "wd3",
    "wd4",
    "wd5",
    "wd6",
    "wd7",
    "wd8",
    "wd9",
    "wd10",
    "wd11",
    "wd12",
    "wd13",
    "wd14",
    "wd15",
    # Measured after the first fifteen were exhausted: wd103 answers with 4,750
    # rows naming 186 tenants in one chunk, while wd16-wd20 answer an empty
    # table (those shard numbers are not in use).
    "wd103",
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
            # times out on this domain.  Exclude what cannot name a board and
            # let the index do the deduplicating instead: measured on one
            # shard, these clauses cut 41.7 rows per distinct board to 25.3
            # (395 boards per 10,000-row chunk instead of ~240) without
            # losing a single board.
            "!original:.*/assets/.*",
            "!original:.*/job/.*",
            # Query strings are crawl noise: a board's identity is its path.
            # They were the largest single block of rows (155 of 1,000).
            r"!original:.*\?.*",
            # Sitemaps, plugin manifests, icons, fonts, styles: never a board.
            r"!original:.*\.(xml|json|ico|txt|png|jpe?g|gif|css|js|svg|woff2?)$",
            r"!original:.*\.well-known.*",
            r"!original:.*/(login|logout|signin|talentcommunity|faq|static|media)(/|$)",
        ),
    },
    "ats.bamboohr": {
        "domain": "bamboohr.com",
        "roots": ("bamboohr.com",),
        "filters": (
            # BambooHR's own marketing site is not a customer board.
            "!original:.*://www[0-9]*[.]bamboohr[.]com.*",
            # Every row is reduced to its subdomain in memory, but the index
            # still charges the whole 10,000-row chunk for a tenant's job
            # pages: those rows name ~450 tenants a chunk.  Dropping any URL
            # with a second path segment leaves the tenant roots behind and
            # names 1,808 tenants per chunk instead (measured, 4x).  A second
            # clause used to make this domain answer 504; measured now: 200
            # with 10,000 rows in 48 s.
            r"!original:.*\.bamboohr\.com/[a-z]{2,}/.*",
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
    "ats.personio": {
        # Personio splits its career sites over two domains (.de and .com); a
        # tenant lives on exactly one, so both are walked.
        "domain": "jobs.personio.de",
        "roots": ("jobs.personio.de", "jobs.personio.com"),
        "filters": (
            # A board is the tenant root; posting pages (/job/{id}) and the XML
            # feed are rows that spend a chunk without naming a new employer.
            "!original:.*/job/[0-9]+.*",
            r"!original:.*\.(xml|json|ico|txt|png|jpe?g|gif|css|js|svg|woff2?)$",
            r"!original:.*\.well-known.*",
        ),
    },
    "ats.breezy": {
        "domain": "breezy.hr",
        "roots": ("breezy.hr",),
        "filters": (
            # /p/{id} is a posting and /json is the feed; neither is a board.
            r"!original:.*\.breezy\.hr/p/.*",
            r"!original:.*\.(xml|json|ico|txt|png|jpe?g|gif|css|js|svg|woff2?)$",
            r"!original:.*\.well-known.*",
        ),
    },
    "ats.jazzhr": {
        "domain": "applytojob.com",
        "roots": ("applytojob.com",),
        "filters": (
            # A board root is ``{tenant}.applytojob.com/apply``; every posting
            # has its own detail page under it, and those rows are what spend a
            # 10,000-row chunk on a handful of employers.
            "!original:.*/apply/jobs/details/.*",
            "!original:.*/apply/[A-Za-z0-9]{6,}/.*",
        ),
    },
    "ats.eightfold": {
        # Eightfold puts the employer in the subdomain and serves the same
        # public listing API its own board calls.  Custom employer hosts
        # (Netflix-style) are not enumerable from this domain, so they stay a
        # manual/runtime-discovery path.
        "domain": "eightfold.ai",
        "roots": ("eightfold.ai",),
        "filters": (
            "!original:.*/careers/job/.*",
            "!original:.*/api/.*",
            r"!original:.*\.(xml|json|ico|txt|png|jpe?g|gif|css|js|svg|woff2?)$",
        ),
    },
    "ats.taleo": {
        # Taleo career sections are server-rendered at
        # ``{tenant}.taleo.net/careersection/{section}``; the section is part of
        # the board's identity because one tenant can publish several.
        "domain": "taleo.net",
        "roots": ("taleo.net",),
        "filters": (
            # The domain walk starts in the apex marketing site (its urlkey
            # ``net,taleo)`` sorts before every tenant's ``net,taleo,{tenant})``)
            # and spent three 10,000-row chunks there naming three boards.
            # ``urlkey`` is the index's own sort key, so requiring a label
            # after the domain keeps every row that can name a tenant.
            "urlkey:net,taleo,.*",
            # Tenant-space rows are overwhelmingly the retired
            # ``/servlets/CareerSection`` shape, which names no career section
            # and so cannot be a board.  Keeping only ``/careersection/`` rows
            # makes each chunk spend on tenants that can.
            "original:.*/careersection/.*",
            "!original:.*/jobdetail.ftl.*",
            r"!original:.*\.(xml|json|ico|txt|png|jpe?g|gif|css|js|svg|woff2?)$",
        ),
    },
    "ats.oracle_recruiting": {
        # Oracle is walked one HCM pod at a time: the whole oraclecloud.com
        # domain is far too large for the gateway, while one pod's domain query
        # answers in 13-45 s (measured).  Each pod keeps its own resume key, so
        # the walk goes *into* a pod's URL-key space instead of sampling only
        # its first rows the way a single unpaged query does.
        "domain": "oraclecloud.com",
        "roots": ORACLE_PODS,
        "filters": (
            "!original:.*/job/.*",
            "!original:.*/requisitions/.*",
            "!original:.*/apply.*",
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
        return board_identity(self.connector_id, self.url, self.tenant_key or self.tenant) or (
            self.connector_id,
            self.tenant_key or self.tenant,
        )


def _host_for(connector_id: str) -> str | None:
    """The preferred fixed host for a connector (first in the rotation)."""
    return next((host for host, conn in SEED_ATS_HOSTS if conn == connector_id), None)


def _board_url(connector_id: str, tenant: str) -> str:
    host = _host_for(connector_id) or SEED_ATS_HOSTS[0][0]
    # Rippling's board is the slug plus ``/jobs``; the bare slug is not a page.
    suffix = "/jobs" if connector_id == "ats.rippling" else ""
    return f"https://{host}/{tenant}{suffix}"


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
    if connector_id == "ats.rippling":
        # One page is 20 rows, so the total is the count that ranks a board.
        if not isinstance(data, dict):
            return 0
        total = data.get("totalItems")
        if isinstance(total, int):
            return total
        items = data.get("items")
        return len(items) if isinstance(items, list) else 0
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
    client: httpx.AsyncClient,
    params: list[tuple[str, str]],
    purpose: str = "",
    attempts: int = HARVEST_ATTEMPTS,
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
    for attempt in range(attempts):
        started = time.monotonic()
        try:
            response = await asyncio.wait_for(
                client.get(query, timeout=CDX_TIMEOUT_S), timeout=CDX_DEADLINE_S
            )
        except TimeoutError:
            # Wall clock ran out with the socket still open: same meaning as an
            # unanswered attempt, but logged apart because it is a different
            # failure from a refused connection or a fast 504.
            _log(
                "cdx_deadline",
                purpose=purpose,
                attempt=attempt + 1,
                deadline_s=CDX_DEADLINE_S,
                elapsed_s=round(time.monotonic() - started, 1),
                **requested,
            )
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
            wait = THROTTLE_BACKOFF_S[attempt] if attempt + 1 < attempts else 0
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
        if attempt + 1 < attempts:
            await asyncio.sleep(THROTTLE_BACKOFF_S[attempt])
    _log("cdx_gave_up", purpose=purpose, attempts=attempts, **requested)
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
    attempts: int = HARVEST_ATTEMPTS,
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
        response = await _cdx_get(
            client, params, purpose=f"slice {host}/{bucket}", attempts=attempts
        )
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


def _replace_file(tmp: Path, path: Path) -> None:
    """Swap a completed temp file into place (a seam so tests can break it)."""
    os.replace(tmp, path)


def _atomic_write(path: Path, text: str) -> None:
    """Write a checkpoint so an interruption cannot corrupt the previous one.

    The builder runs for hours (archive sweeps plus tens of thousands of
    verifications) and can be stopped at any moment.  Writing straight to the
    checkpoint would leave a truncated JSON file after a kill mid-write, and
    the next run would silently discard hours of work because it cannot parse
    the file.  A sibling temp file plus an atomic rename means a reader sees
    either the old complete checkpoint or the new complete one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_file(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _checkpoint(completed: set[tuple[str, str]], boards: dict[Board, int]) -> None:
    _atomic_write(
        CACHE_PATH,
        json.dumps(
            {
                "completed": [[host, bucket] for host, bucket in sorted(completed)],
                "results": {
                    f"{board.connector_id}|{board.tenant}": count for board, count in boards.items()
                },
            },
            indent=1,
        ),
    )


async def harvest_all(client: httpx.AsyncClient) -> dict[Board, int]:
    """Harvest every fixed-host slice, reusing checkpointed results.

    Slices run a few at a time, and a slice the gateway truncated (a full
    ``CDX_LIMIT`` rows) is split into two-character buckets and walked again,
    so a single crowded bucket cannot hide the long tail behind it.

    A slice that never answered stays out of ``completed`` so the next run
    retries it; therefore a resumed run still has to look at the queue, or an
    unanswered slice becomes a permanent gap in the seed.
    """
    completed, cached = _read_harvest_cache()
    boards: dict[Board, int] = defaultdict(int, cached)
    if completed:
        _log("harvest_resume", slices=len(completed), candidates=len(boards))

    queue: list[tuple[str, str]] = [
        (host, bucket)
        for host, _connector_id in SEED_ATS_HOSTS
        for bucket in BUCKETS
        if (host, bucket) not in completed
    ]
    done = len(completed)
    started = time.monotonic()
    semaphore = asyncio.Semaphore(SLICE_CONCURRENCY)

    async def run_one(host: str, bucket: str, attempts: int) -> tuple[str, str, int, bool]:
        connector_id = next(connector for h, connector in SEED_ATS_HOSTS if h == host)
        result = await harvest_slice(client, host, bucket, semaphore, attempts)
        if result is None:
            # Left out of ``completed`` on purpose: the next run retries it.
            return host, bucket, -1, False
        rows, counts = result
        for tenant, count in counts.items():
            boards[_board_for(connector_id, tenant)] += count
        completed.add((host, bucket))
        truncated = rows >= CDX_LIMIT and len(bucket) < MAX_BUCKET_DEPTH
        return host, bucket, rows, truncated

    idle_since: float | None = None
    while queue:
        batch = queue[:SLICE_CONCURRENCY]
        del queue[:SLICE_CONCURRENCY]
        # While nothing is answering, ask once per slice and give the gateway
        # time instead of spending four attempts on a request that has been
        # timing out; the queue stays checkpointed either way.
        attempts = 1 if idle_since is not None else HARVEST_ATTEMPTS
        outcomes = await asyncio.gather(
            *(run_one(host, bucket, attempts) for host, bucket in batch),
            return_exceptions=True,
        )
        answered = 0
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                _log("slice_failed", error=type(outcome).__name__)
                continue
            host, bucket, rows, truncated = outcome
            if rows < 0:
                _log("slice_unanswered", host=host, bucket=bucket, retry="next run")
                continue
            answered += 1
            done += 1
            if truncated:
                _log("slice_truncated", host=host, bucket=bucket, rows=rows)
                queue.extend(
                    (host, bucket + char)
                    for char in BUCKETS
                    if (host, bucket + char) not in completed
                )
        if answered:
            idle_since = None
        else:
            now = time.monotonic()
            idle_since = now if idle_since is None else idle_since
            idle = now - idle_since
            if idle >= CDX_IDLE_BUDGET_S:
                _log(
                    "slices_abandoned",
                    pending=len(queue),
                    idle_s=round(idle),
                    done=done,
                )
                _checkpoint(completed, boards)
                break
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
    if connector_id == "ats.oracle_recruiting":
        # An Oracle board needs the host *and* the site code, so it cannot go
        # through the ``{tenant}.{domain}`` reading below.
        return _oracle_board(url)
    parts = urlsplit(url)
    # ``hostname`` drops the userinfo and the port: the index carries rows like
    # ``http://aa.taleo.net:80/servlets/CareerSection``, and a port left in the
    # host fails the ``.{domain}`` suffix test and silently loses the tenant.
    host = MANGLED_HOST_RE.sub("", (parts.hostname or "").lower())
    cfg = SUBDOMAIN_ATS[connector_id]
    domain = next(
        (
            candidate
            for candidate in (cfg["domain"], *SUBDOMAIN_ALIAS_DOMAINS.get(connector_id, ()))
            if host.endswith("." + candidate)
        ),
        None,
    )
    if domain is None:
        return None
    suffix = "." + domain
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
    if connector_id == "ats.jazzhr":
        if len(labels) != 1 or "." in sub:
            return None
        if sub in RESERVED_SUBDOMAINS or not TENANT_RE.match(sub):
            return None
        return Board(connector_id, sub, f"https://{host}/apply", tenant_key=sub)
    if connector_id == "ats.eightfold":
        if len(labels) != 1 or "." in sub:
            return None
        if sub in RESERVED_SUBDOMAINS or sub == "www" or not TENANT_RE.match(sub):
            return None
        return Board(connector_id, sub, f"https://{host}/careers", tenant_key=sub)
    if connector_id == "ats.taleo":
        if len(labels) != 1 or "." in sub:
            return None
        if sub in RESERVED_SUBDOMAINS or not TENANT_RE.match(sub):
            return None
        match = TALEO_PATH_RE.match(parts.path)
        if match is None:
            return None
        section = match.group("section")
        return Board(
            connector_id,
            sub,
            f"https://{host}/careersection/{section}/jobsearch.ftl?lang=en",
            tenant_key=f"{sub}|{section.lower()}",
        )
    if connector_id == "ats.personio":
        if len(labels) != 1 or "." in sub or sub in RESERVED_SUBDOMAINS:
            return None
        if not TENANT_RE.match(sub):
            return None
        return Board(connector_id, sub, f"https://{host}/", tenant_key=sub)
    if connector_id == "ats.breezy":
        if len(labels) != 1 or "." in sub or sub in RESERVED_SUBDOMAINS:
            return None
        if not TENANT_RE.match(sub):
            return None
        return Board(connector_id, sub, f"https://{host}", tenant_key=sub)
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


PAYLOCITY_PATH_RE = re.compile(
    r"^/recruiting/jobs/all/(?P<company>"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:/|$)",
    re.IGNORECASE,
)


def _paylocity_board(url: str) -> Board | None:
    """A Paylocity board from one indexed URL, or ``None`` for site chrome.

    The company id is the board; the readable slug after it is decorative and
    one employer is indexed under several slugs, so the slug is dropped.
    """
    parts = urlsplit(url)
    if parts.netloc.lower() != "recruiting.paylocity.com":
        return None
    match = PAYLOCITY_PATH_RE.match(parts.path.lower())
    if match is None:
        return None
    company = match.group("company")
    return Board(
        "ats.paylocity",
        company,
        f"https://recruiting.paylocity.com/Recruiting/Jobs/All/{company}",
        tenant_key=company,
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
        _atomic_write(SUBDOMAIN_CACHE_PATH, json.dumps(cache, indent=1))

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


def _oracle_board(url: str) -> Board | None:
    """The Candidate Experience site an Oracle URL names, if any.

    Identity is ``host`` plus ``site``: one Oracle tenant host can publish
    several sites (recruiting, campus, exec), and each is its own board, while
    ``/en`` and ``/fr-CA`` are the same site mirrored.
    """
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if not host.endswith(".oraclecloud.com"):
        return None
    # The pod itself (``fa.us2.oraclecloud.com``) is Oracle's own host, not a
    # tenant: a tenant host has its label in front of the pod's four.
    labels = host.split(".")
    if len(labels) < 5:
        return None
    match = ORACLE_PATH_RE.match(parts.path)
    if match is None:
        return None
    site = match.group("site")
    lang = match.group("lang") or "en"
    return Board(
        "ats.oracle_recruiting",
        labels[0],
        f"https://{host}/hcmUI/CandidateExperience/{lang}/sites/{site}",
        # The same site is indexed under several spellings (``WerkenBij`` and
        # ``werkenbij``), so identity folds the site code's case: otherwise one
        # board is seeded twice with URLs that differ only in case.
        tenant_key=f"{host}|{site.lower()}",
    )


def _oracle_site(board: Board) -> str | None:
    """The ``sites/{code}`` segment of an Oracle board URL."""
    match = ORACLE_PATH_RE.match(urlsplit(board.url).path)
    return match.group("site") if match else None


#: Extra career-site domains a subdomain ATS publishes the same board shape
#: on.  Personio tenants are split across ``.jobs.personio.de`` and
#: ``.jobs.personio.com``; the suffix parser has to accept either.
SUBDOMAIN_ALIAS_DOMAINS: dict[str, tuple[str, ...]] = {
    "ats.personio": ("jobs.personio.com",),
}

#: Path-addressed ATSs: one host, the board's identity inside the path.
PATH_BOARDS: dict[str, _PathCfg] = {
    "ats.ultipro": {
        "domain": ULTIPRO_DOMAIN,
        "filter": "original:.*/JobBoard/.*",
        "parse": _ultipro_board,
    },
}

#: One prefix query per company-id initial, in each spelling of the path a
#: site uses (the index has both ``/Recruiting/Jobs/All/`` and
#: ``/recruiting/jobs/all/`` and prefix matching is case-sensitive).
PAYLOCITY_SLICES = tuple(
    f"recruiting.paylocity.com{path}/{initial}"
    for path in ("/Recruiting/Jobs/All", "/recruiting/jobs/all")
    for initial in "0123456789abcdef"
)

RIPPLING_PATH_RE = re.compile(
    r"^/(?P<tenant>[a-z0-9][a-z0-9-]{1,62})(?:/|$)", re.IGNORECASE
)
#: One prefix query per slug initial: Rippling boards share one host and the
#: slug is the first path segment, so a bucket query names a whole alphabet.
RIPPLING_SLICES = tuple(f"ats.rippling.com/{initial}" for initial in BUCKETS)


def _rippling_board(url: str) -> Board | None:
    """A Rippling board from one indexed URL, or ``None`` for site chrome.

    The slug is the first path segment and the same slug is indexed under job
    pages (``/{slug}/jobs/{id}``) and the board itself, so both collapse onto
    the board URL.
    """
    parts = urlsplit(url)
    if parts.netloc.lower() != "ats.rippling.com":
        return None
    match = RIPPLING_PATH_RE.match(parts.path)
    if match is None:
        return None
    tenant = match.group("tenant").lower()
    if tenant in RESERVED_SUBDOMAINS or tenant in {"api", "jobs", "job", "careers"}:
        return None
    return Board(
        "ats.rippling",
        tenant,
        f"https://ats.rippling.com/{tenant}/jobs",
        tenant_key=tenant,
    )


PREFIX_BOARDS: dict[str, _PrefixCfg] = {
    "ats.paylocity": {"slices": PAYLOCITY_SLICES, "parse": _paylocity_board},
    "ats.rippling": {"slices": RIPPLING_SLICES, "parse": _rippling_board},
}


async def harvest_path_boards(
    client: httpx.AsyncClient, connector_id: str, chunks: int, resume: bool
) -> dict[Board, int]:
    """Walk a path-addressed ATS in resumable 10,000-row chunks.

    UKG Pro (``/JobBoard/{id}``) and Paylocity (``/Recruiting/Jobs/All/{id}``)
    both address the board inside the path on one host, so a single walker
    serves both: it pages the host behind a mid-path filter and reads each row
    with that connector's own parser.
    """
    cfg = PATH_BOARDS[connector_id]
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
    stored_key = state.get("key")
    resume_key: str | None = stored_key if isinstance(stored_key, str) else None
    stored_chunks = state.get("chunks", 0)
    done = stored_chunks if isinstance(stored_chunks, int) else 0
    if resume and done and (not resume_key or done >= chunks):
        _log("resume_cached", connector=connector_id, chunks=done, candidates=len(boards))
        return dict(boards)

    started = time.monotonic()
    idle_since: float | None = None
    while done < chunks:
        params: list[tuple[str, str]] = [
            ("url", cfg["domain"]),
            ("matchType", "domain"),
            ("fl", "original"),
            ("collapse", "urlkey"),
            ("limit", str(CDX_LIMIT)),
            ("output", "json"),
            ("showResumeKey", "true"),
            ("filter", cfg["filter"]),
        ]
        if resume_key:
            params.append(("resumeKey", resume_key))
        _log(
            "chunk_start",
            connector=connector_id,
            root=cfg["domain"],
            chunk=f"{done + 1}/{chunks}",
            has_resume_key=bool(resume_key),
        )
        await asyncio.sleep(REQUEST_GAP_S)
        response = await _cdx_get(client, params, purpose=f"{connector_id} {cfg['domain']}")
        urls: list[str] = []
        next_resume_key: str | None = None
        parsed = False
        if response is None:
            _log("chunk_unanswered", connector="ats.ultipro", root=cfg["domain"])
        else:
            urls, next_resume_key, parsed = _chunk_urls(response.text)
            if not parsed:
                _log(
                    "chunk_unparsed",
                    connector=connector_id,
                    root=cfg["domain"],
                    bytes=len(response.content),
                    body=_preview(response.text),
                )
        if not parsed:
            # A throttled chunk says nothing about the rows still behind it and
            # the resume key still stands, so retry instead of abandoning the
            # walk: one bad gateway window used to end the whole connector run.
            now = time.monotonic()
            idle_since = now if idle_since is None else idle_since
            idle = now - idle_since
            if idle >= CDX_IDLE_BUDGET_S:
                _log("roots_abandoned", connector="ats.ultipro", chunks=done, idle_s=round(idle))
                break
            _log(
                "chunk_retrying",
                connector=connector_id,
                chunks=done,
                idle_s=round(idle),
                budget_s=CDX_IDLE_BUDGET_S,
            )
            continue
        idle_since = None
        resume_key = next_resume_key
        before = len(boards)
        for url in urls:
            candidate = cfg["parse"](url)
            if candidate is not None:
                boards[candidate] += 1
        done += 1
        cache[connector_id] = {
            "chunks": done,
            "key": resume_key,
            "boards": {
                f"{b.connector_id}|{b.tenant}|{b.url}|{b.tenant_key or ''}": n
                for b, n in boards.items()
            },
        }
        _atomic_write(SUBDOMAIN_CACHE_PATH, json.dumps(cache, indent=1))
        _log(
            "chunk_done",
            connector=connector_id,
            root=cfg["domain"],
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


async def harvest_prefix_boards(
    client: httpx.AsyncClient, connector_id: str, budget: int, resume: bool
) -> dict[Board, int]:
    """Walk a shared-host ATS through URL-prefix slices.

    Measured while the archive is throttling: a filtered *domain* scan -- even
    one narrowed to a single tenant initial -- times out, while a prefix query
    answers in seconds.  Paylocity addresses every board inside a path, so its
    board space is covered by slicing that path instead: one query per company
    id initial, and ``budget`` requests in total.
    """
    cfg = PREFIX_BOARDS[connector_id]
    slices = cfg["slices"]
    parse = cfg["parse"]
    cache: dict[str, object] = {}
    if SUBDOMAIN_CACHE_PATH.exists():
        try:
            raw = json.loads(SUBDOMAIN_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cache = raw
        except (OSError, ValueError):
            cache = {}
    slot = f"{connector_id}|prefix"
    stored = cache.get(slot)
    state: dict[str, object] = stored if isinstance(stored, dict) else {}

    boards: dict[Board, int] = defaultdict(int)
    for key, count in (state.get("boards") or {}).items():  # type: ignore[union-attr]
        parts = str(key).split("|", 3)
        if len(parts) == 4:
            boards[Board(parts[0], parts[1], parts[2], parts[3] or None)] += int(count)
    done = {str(item) for item in (state.get("slices") or [])}  # type: ignore[union-attr]
    keys = {
        str(name): str(value)
        for name, value in (state.get("keys") or {}).items()  # type: ignore[union-attr]
        if value
    }
    stored_spent = state.get("requests", 0)
    spent = stored_spent if isinstance(stored_spent, int) else 0
    if resume and len(done) >= len(slices):
        _log("resume_cached", connector=connector_id, slices=len(done), candidates=len(boards))
        return dict(boards)

    started = time.monotonic()
    for prefix in slices:
        if prefix in done:
            continue
        while spent < budget:
            params: list[tuple[str, str]] = [
                ("url", prefix),
                ("matchType", "prefix"),
                ("fl", "original"),
                ("collapse", "urlkey"),
                ("limit", str(CDX_LIMIT)),
                ("output", "json"),
                ("showResumeKey", "true"),
            ]
            if keys.get(prefix):
                params.append(("resumeKey", keys[prefix]))
            await asyncio.sleep(REQUEST_GAP_S)
            response = await _cdx_get(client, params, purpose=f"{connector_id} {prefix}")
            spent += 1
            if response is None:
                _log("slice_unanswered", connector=connector_id, prefix=prefix)
                break
            urls, next_key, parsed = _chunk_urls(response.text)
            if not parsed:
                _log(
                    "slice_unparsed",
                    connector=connector_id,
                    prefix=prefix,
                    bytes=len(response.content),
                    body=_preview(response.text),
                )
                break
            before = len(boards)
            for url in urls:
                candidate = parse(url)
                if candidate is not None:
                    boards[candidate] += 1
            if next_key:
                keys[prefix] = next_key
            else:
                done.add(prefix)
                keys.pop(prefix, None)
            cache[slot] = {
                "slices": sorted(done),
                "keys": keys,
                "requests": spent,
                "boards": {
                    f"{b.connector_id}|{b.tenant}|{b.url}|{b.tenant_key or ''}": n
                    for b, n in boards.items()
                },
            }
            _atomic_write(SUBDOMAIN_CACHE_PATH, json.dumps(cache, indent=1))
            _log(
                "slice_done",
                connector=connector_id,
                prefix=prefix,
                rows=len(urls),
                added=len(boards) - before,
                candidates=len(boards),
                ended=not next_key,
                requests=spent,
                budget=budget,
                elapsed_s=round(time.monotonic() - started),
            )
            if not next_key:
                break
        if spent >= budget:
            _log(
                "prefix_budget_exhausted",
                connector=connector_id,
                slices=len(done),
                requests=spent,
                budget=budget,
            )
            break
    return dict(boards)


def bucket_children(prefix: str) -> tuple[str, ...]:
    """Every one-character refinement of a bucket prefix, in sweep order."""
    return tuple(f"{prefix}{char}" for char in BUCKETS)


def bucket_is_complete(prefix: str, done: set[str]) -> bool:
    """Whether a bucket is harvested whole, or completely through its parts."""
    if prefix in done:
        return True
    if len(prefix) >= BUCKET_SPLIT_DEPTH:
        return False
    return all(bucket_is_complete(child, done) for child in bucket_children(prefix))


def bucket_split_started(prefix: str, done: set[str]) -> bool:
    """Whether an earlier pass already fell back to sub-ranges of a bucket."""
    return prefix not in done and any(
        bucket_is_complete(child, done) for child in bucket_children(prefix)
    )


async def harvest_initial_buckets(
    client: httpx.AsyncClient,
    connector_id: str,
    resume: bool,
    budget_s: float = BUCKET_BUDGET_S,
) -> dict[Board, int]:
    """Enumerate a subdomain ATS one tenant-initial bucket at a time.

    The archive's resume key does not advance through a *filtered* row space:
    measured on BambooHR, every chunk after the first carried a resume key and
    still returned the same 10,000 rows naming no new tenant, so a filtered
    walk stalls.  A bucket on the tenant's first character needs no pagination
    at all -- 36 mutually exclusive queries cover the whole tenant space, and
    each one answers in seconds.
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
    slot = f"{connector_id}|buckets"
    stored = cache.get(slot)
    state: dict[str, object] = stored if isinstance(stored, dict) else {}
    boards: dict[Board, int] = defaultdict(int, _ct_cached_boards(state))
    done = {str(item) for item in (state.get("buckets") or [])}
    if resume and all(bucket_is_complete(initial, done) for initial in BUCKETS):
        _log("resume_cached", connector=connector_id, buckets=len(done), candidates=len(boards))
        return dict(boards)

    started = time.monotonic()
    stalled = 0

    def save() -> None:
        cache[slot] = {
            "buckets": sorted(done),
            "boards": {
                f"{b.connector_id}|{b.tenant}|{b.url}|{b.tenant_key or ''}": n
                for b, n in boards.items()
            },
        }
        _atomic_write(SUBDOMAIN_CACHE_PATH, json.dumps(cache, indent=1))

    def record(prefix: str, urls: list[str], shape_used: str) -> None:
        before = len(boards)
        for url in urls:
            candidate = _subdomain_board(connector_id, url)
            if candidate is not None:
                boards[candidate] += 1
        done.add(prefix)
        save()
        _log(
            "bucket_done",
            connector=connector_id,
            bucket=prefix,
            shape=shape_used,
            rows=len(urls),
            # A full allowance means the bucket was cut off mid-alphabet: its
            # tenants are real but incomplete, and its sub-ranges are read too.
            truncated=len(urls) >= CDX_LIMIT,
            added=len(boards) - before,
            candidates=len(boards),
            elapsed_s=round(time.monotonic() - started),
        )

    async def query(prefix: str, attempts: int) -> tuple[list[str], bool, str]:
        """Ask the index for one bucket prefix, densest shape first."""
        # Positive clause: one tenant initial only, so the gateway never has to
        # page and a crowded tenant's job pages cannot crowd out the names
        # behind them.
        positive = f"original:https?://{prefix}[a-z0-9-]*[.]{cfg['domain']}.*"
        # Two shapes for the same bucket.  The cheap one asks for the tenant
        # initial alone; the dense one also applies the connector's own
        # clauses, which drop job pages and marketing hosts so a full
        # 10,000-row allowance names far more tenants (measured on BambooHR:
        # 4x).  The cheap shape goes first because it is the one that answers
        # while the gateway is refusing scans, and a full answer is now split
        # into sub-ranges instead of being trusted -- which reads more tenants
        # than the dense clause ever squeezed in.
        dense = ("dense", (positive, *cfg["filters"]))
        plain = ("plain", (positive,))
        shapes = (plain,) if len(prefix) > 1 else (plain, dense)
        for shape, clauses in shapes:
            params: list[tuple[str, str]] = [
                ("url", cfg["domain"]),
                ("matchType", "domain"),
                ("fl", "original"),
                ("collapse", "urlkey"),
                ("limit", str(CDX_LIMIT)),
                ("output", "json"),
            ]
            params.extend(("filter", clause) for clause in clauses)
            await asyncio.sleep(REQUEST_GAP_S)
            response = await _cdx_get(
                client,
                params,
                purpose=f"{connector_id} bucket {prefix} {shape}",
                attempts=attempts,
            )
            if response is None:
                _log(
                    "bucket_unanswered",
                    connector=connector_id,
                    bucket=prefix,
                    shape=shape,
                    retry="fallback" if shape != shapes[-1][0] else "split",
                )
                continue
            urls, _key, parsed = _chunk_urls(response.text)
            if not parsed:
                _log(
                    "bucket_unparsed",
                    connector=connector_id,
                    bucket=prefix,
                    shape=shape,
                    bytes=len(response.content),
                    body=_preview(response.text),
                )
                continue
            return urls, True, shape
        return [], False, ""

    async def sweep_children(prefix: str) -> bool:
        """Read a refused or truncated bucket as its mutually exclusive parts."""
        complete = True
        for child in bucket_children(prefix):
            if not await walk(child):
                complete = False
            if time.monotonic() - started >= budget_s:
                return False
        if complete:
            done.add(prefix)
            save()
            _log(
                "bucket_split_done",
                connector=connector_id,
                bucket=prefix,
                children=len(bucket_children(prefix)),
                candidates=len(boards),
            )
        return complete

    async def walk(prefix: str) -> bool:
        """Harvest one bucket; partition it rather than give up when refused."""
        nonlocal stalled
        if prefix in done:
            return True
        if time.monotonic() - started >= budget_s:
            return False
        if bucket_split_started(prefix, done):
            # An earlier pass already fell back to this bucket's sub-ranges:
            # finish those instead of re-issuing the query it refused.
            _log("bucket_split_resume", connector=connector_id, bucket=prefix)
            return await sweep_children(prefix)
        # While the gateway is failing every bucket, probe each shape once
        # instead of twice: the point is to meet the next healthy window
        # sooner, not to insist on a query that has been timing out.
        # A sub-range is one of many probes, so it never spends a retry the
        # whole bucket would get.
        attempts = 1 if stalled or len(prefix) > 1 else 2
        urls, parsed, shape_used = await query(prefix, attempts)
        if not parsed:
            if len(prefix) < BUCKET_SPLIT_DEPTH:
                # The gateway refused the whole bucket.  Partitioning it is the
                # degradation that keeps a sweep alive: every sub-range is its
                # own cheaper query, and each one that answers is checkpointed
                # now, so a killed run resumes from there.
                _log(
                    "bucket_split",
                    connector=connector_id,
                    bucket=prefix,
                    reason="refused",
                    children=len(bucket_children(prefix)),
                )
                complete = await sweep_children(prefix)
                if not complete:
                    stalled += 1
                    await asyncio.sleep(BUCKET_PAUSE_S)
                return complete
            # At the deepest split the bucket stays undone and the sweep waits
            # before probing the next one, so a throttled gateway costs it a
            # pause rather than the rest of the alphabet.
            stalled += 1
            await asyncio.sleep(BUCKET_PAUSE_S)
            return False
        stalled = 0
        record(prefix, urls, shape_used)
        if len(urls) >= CDX_LIMIT and len(prefix) < BUCKET_SPLIT_DEPTH:
            _log(
                "bucket_truncated_split",
                connector=connector_id,
                bucket=prefix,
                rows=len(urls),
            )
            await sweep_children(prefix)
        return True

    for initial in BUCKETS:
        await walk(initial)
        if time.monotonic() - started >= budget_s:
            _log(
                "bucket_sweep_budget_exhausted",
                connector=connector_id,
                buckets=len(done),
                candidates=len(boards),
                budget_s=budget_s,
                unanswered=stalled,
            )
            break
    _log("bucket_sweep_done", connector=connector_id, buckets=len(done), candidates=len(boards))
    return dict(boards)


async def harvest_shard_buckets(
    client: httpx.AsyncClient,
    connector_id: str,
    budget_s: float = BUCKET_BUDGET_S,
) -> dict[Board, int]:
    """Walk a sharded subdomain ATS one tenant initial at a time.

    Workday spreads tenants over ``wd1``..``wd15`` and a filtered domain chunk
    cannot page past its first rows, so each shard is walked by leading
    character instead.  A bucket that comes back completely full is split into
    two-character buckets and walked again, which is how the fixed-host walker
    already avoids stopping at whatever the index lists first.
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
    slot = f"{connector_id}|shards"
    stored = cache.get(slot)
    state: dict[str, object] = stored if isinstance(stored, dict) else {}
    boards: dict[Board, int] = defaultdict(int)
    for key, count in (state.get("boards") or {}).items():
        parts = str(key).split("|", 3)
        if len(parts) == 4:
            boards[Board(parts[0], parts[1], parts[2], parts[3] or None)] += int(count)
    done = {str(item) for item in (state.get("buckets") or [])}

    def save() -> None:
        cache[slot] = {
            "buckets": sorted(done),
            "boards": {
                f"{b.connector_id}|{b.tenant}|{b.url}|{b.tenant_key or ''}": n
                for b, n in boards.items()
            },
        }
        _atomic_write(SUBDOMAIN_CACHE_PATH, json.dumps(cache, indent=1))

    started = time.monotonic()
    stalled = 0

    def prefixes_of(root: str) -> set[str]:
        marker = f"{root}|"
        return {entry[len(marker) :] for entry in done if entry.startswith(marker)}

    async def sweep_children(root: str, prefix: str, depth: int) -> None:
        """Read a refused or truncated shard bucket as its exclusive parts."""
        for char in BUCKETS:
            await walk(root, prefix + char, depth + 1)
            if time.monotonic() - started >= budget_s:
                return
        if all(f"{root}|{prefix}{char}" in done for char in BUCKETS):
            done.add(f"{root}|{prefix}")
            save()
            _log(
                "shard_bucket_split_done",
                connector=connector_id,
                root=root,
                bucket=prefix,
                candidates=len(boards),
            )

    async def walk(root: str, prefix: str, depth: int) -> None:
        nonlocal stalled
        key = f"{root}|{prefix}"
        if key in done or time.monotonic() - started >= budget_s:
            return
        if bucket_split_started(prefix, prefixes_of(root)):
            # An earlier pass already fell back to this bucket's sub-ranges:
            # finish those instead of re-issuing the query it refused.
            _log("shard_bucket_split_resume", connector=connector_id, root=root, bucket=prefix)
            await sweep_children(root, prefix, depth)
            return
        positive = f"original:https?://{prefix}[a-z0-9-]*[.]{root}.*"
        # Same ladder as the single-domain sweep: the cheap shape first, and a
        # sub-range probes once because it is one of many.
        dense = ("dense", (positive, *cfg["filters"]))
        plain = ("plain", (positive,))
        shapes = (plain,) if len(prefix) > 1 else (plain, dense)
        attempts = 1 if stalled or len(prefix) > 1 else 2
        urls: list[str] = []
        parsed = False
        shape_used = ""
        for shape, clauses in shapes:
            params: list[tuple[str, str]] = [
                ("url", root),
                ("matchType", "domain"),
                ("fl", "original"),
                ("collapse", "urlkey"),
                ("limit", str(CDX_LIMIT)),
                ("output", "json"),
            ]
            params.extend(("filter", clause) for clause in clauses)
            await asyncio.sleep(REQUEST_GAP_S)
            response = await _cdx_get(
                client, params, purpose=f"{connector_id} shard {key} {shape}", attempts=attempts
            )
            if response is None:
                continue
            urls, _next_key, parsed = _chunk_urls(response.text)
            if not parsed:
                continue
            shape_used = shape
            break
        if not parsed:
            if len(prefix) < BUCKET_SPLIT_DEPTH:
                # The gateway refused the whole shard bucket: partition it so
                # every sub-range that answers is checkpointed now.
                _log(
                    "shard_bucket_split",
                    connector=connector_id,
                    root=root,
                    bucket=prefix,
                    reason="refused",
                )
                await sweep_children(root, prefix, depth)
                return
            stalled += 1
            await asyncio.sleep(BUCKET_PAUSE_S)
            return
        stalled = 0
        if len(urls) >= CDX_LIMIT and depth < 2:
            await sweep_children(root, prefix, depth)
        before = len(boards)
        for url in urls:
            candidate = _subdomain_board(connector_id, url)
            if candidate is not None:
                boards[candidate] += 1
        done.add(key)
        save()
        _log(
            "shard_bucket_done",
            connector=connector_id,
            root=root,
            bucket=prefix,
            shape=shape_used,
            rows=len(urls),
            added=len(boards) - before,
            candidates=len(boards),
        )

    for root in cfg["roots"]:
        for initial in BUCKETS:
            await walk(root, initial, 0)
            if time.monotonic() - started >= budget_s:
                break
        if time.monotonic() - started >= budget_s:
            break
    save()
    _log(
        "shard_buckets_done",
        connector=connector_id,
        buckets=len(done),
        candidates=len(boards),
        elapsed_s=round(time.monotonic() - started),
    )
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
    cfg = SUBDOMAIN_ATS[connector_id]
    domain = next(
        (
            candidate
            for candidate in (cfg["domain"], *SUBDOMAIN_ALIAS_DOMAINS.get(connector_id, ()))
            if host.endswith("." + candidate)
        ),
        None,
    )
    if domain is None:
        return None
    suffix = "." + domain
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
        _atomic_write(CT_CACHE_PATH, json.dumps(cache, indent=1))

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
    _atomic_write(CT_CACHE_PATH, json.dumps(cache, indent=1))
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


async def _verify_jazzhr(
    client: httpx.AsyncClient, board: Board, sem: asyncio.Semaphore
) -> int | None:
    """Live JazzHR board: its own job list, served by the tenant's host.

    An unknown tenant is not a 404: JazzHR redirects it to
    ``www.jazzhr.com`` and answers **200 with the marketing site** (measured),
    so the answer has to stay on the tenant's own host to count.
    """
    host = urlsplit(board.url).netloc.lower()
    async with sem:
        await asyncio.sleep(0.05)
        for attempt in range(VERIFY_ATTEMPTS):
            try:
                response = await client.get(
                    f"https://{host}/apply/jobs", timeout=20.0, follow_redirects=True
                )
            except httpx.HTTPError:
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code == 200:
                if urlsplit(str(response.url)).netloc.lower() != host:
                    return None
                return len(set(JAZZHR_DETAIL_RE.findall(response.text)))
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            return None
    return _UNREACHABLE


_PACE_LOCK = asyncio.Lock()
_PACE_NEXT: dict[str, float] = {}


async def _pace(host: str) -> None:
    """Wait for this host's next request slot.

    The slot is reserved before sleeping instead of holding the lock through
    the wait, so concurrent verifiers queue up at the measured rate instead of
    racing into the rate limiter.
    """
    gap = HOST_PACE_S.get(host)
    if not gap:
        return
    async with _PACE_LOCK:
        slot = max(time.monotonic(), _PACE_NEXT.get(host, 0.0))
        _PACE_NEXT[host] = slot + gap
    delay = slot - time.monotonic()
    if delay > 0:
        await asyncio.sleep(delay)


def _retry_after(response: httpx.Response, default: float = 5.0) -> float:
    """The seconds a 429 asks us to wait."""
    try:
        return max(0.5, float(response.headers.get("retry-after", "")))
    except ValueError:
        return default


async def _verify_paylocity(
    client: httpx.AsyncClient, board: Board, sem: asyncio.Semaphore
) -> int | None:
    """Live Paylocity board: the company id's own job list.

    A retired company id still renders an empty page, so the id has to appear
    in the answer's own URL: that is what separates "this board has no open
    roles today" from "this company is gone".
    """
    company = (board.tenant_key or board.tenant).lower()
    host = "recruiting.paylocity.com"
    async with sem:
        for attempt in range(VERIFY_ATTEMPTS):
            await _pace(host)
            try:
                response = await client.get(board.url, timeout=20.0, follow_redirects=True)
            except httpx.HTTPError:
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code == 200:
                if company not in str(response.url).lower():
                    return None
                return len(set(PAYLOCITY_DETAIL_RE.findall(response.text)))
            if response.status_code == 429:
                # Honour the host's own countdown instead of guessing.
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(_retry_after(response))
                continue
            if response.status_code in (500, 502, 503, 504):
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            return None
    return _UNREACHABLE


async def _verify_oracle(
    client: httpx.AsyncClient, board: Board, sem: asyncio.Semaphore
) -> int | None:
    """Live Oracle Recruiting Cloud board: its own requisition finder.

    The Candidate Experience frontend calls this REST resource without auth,
    and ``TotalJobsCount`` is the site's own number of open requisitions.  A
    tenant behind a WAF answers 403, which is not proof either way; it is
    reported as unreachable so a later run can ask again.
    """
    host = urlsplit(board.url).netloc.lower()
    site = _oracle_site(board)
    if not host or not site:
        return None
    url = (
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        f"?onlyData=true&finder=findReqs;siteNumber={site},limit=1,offset=0"
    )
    async with sem:
        await asyncio.sleep(0.05)
        for attempt in range(VERIFY_ATTEMPTS):
            try:
                response = await client.get(url, timeout=20.0, follow_redirects=True)
            except httpx.HTTPError:
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError:
                    return None
                items = data.get("items") if isinstance(data, dict) else None
                if not isinstance(items, list) or not items:
                    return None
                head = items[0]
                if not isinstance(head, dict):
                    return None
                total = head.get("TotalJobsCount")
                if isinstance(total, int):
                    return total
                jobs = head.get("requisitionList")
                return len(jobs) if isinstance(jobs, list) else 0
            if response.status_code in (403, 429, 500, 502, 503, 504):
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


async def _verify_eightfold(
    client: httpx.AsyncClient, board: Board, sem: asyncio.Semaphore
) -> int | None:
    """Live Eightfold board: the public ``apply/v2/jobs`` listing.

    The endpoint needs the employer's web domain, which is not the tenant
    label: the board page declares it (``"domain": "acme.com"``), and only
    then is ``{tenant}.com`` guessed.  A board the API does not know answers
    without a ``count``, which is not proof either way, so it is retried by a
    later run rather than dropped.
    """
    parts = urlsplit(board.url)
    host = parts.netloc.lower()
    tenant = (board.tenant_key or board.tenant).lower()
    edge = "/careers" if "://" not in board.url else parts.path or "/careers"
    async with sem:
        await asyncio.sleep(0.05)
        domain: str | None = None
        try:
            page = await client.get(f"https://{host}{edge}", timeout=20.0, follow_redirects=True)
        except httpx.HTTPError:
            page = None
        if page is not None and page.status_code == 200:
            match = EIGHTFOLD_DOMAIN_RE.search(page.text)
            if match:
                domain = match.group(1)
        for candidate_domain in ([domain] if domain else []) + [f"{tenant}.com"]:
            url = (
                f"https://{host}/api/apply/v2/jobs?"
                f"{urlencode({'domain': candidate_domain, 'start': 0, 'num': 1})}"
            )
            for attempt in range(VERIFY_ATTEMPTS):
                try:
                    response = await client.get(url, timeout=20.0, follow_redirects=True)
                except httpx.HTTPError:
                    if attempt + 1 < VERIFY_ATTEMPTS:
                        await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                if response.status_code == 200:
                    try:
                        data = response.json()
                    except ValueError:
                        break
                    if isinstance(data, dict):
                        total = data.get("count")
                        if isinstance(total, int):
                            return total
                        positions = data.get("positions")
                        if isinstance(positions, list):
                            return len(positions)
                    break
                if response.status_code in (403, 429, 500, 502, 503, 504):
                    if attempt + 1 < VERIFY_ATTEMPTS:
                        await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                break
    return _UNREACHABLE


async def _taleo_postings_of(
    client: httpx.AsyncClient, host: str, portal: str, referer: str
) -> int | None:
    """A Taleo section's own posting count, from its faceted-search endpoint.

    The endpoint needs the caller's time zone headers and the section's portal
    number; without them it answers an empty frame or a bare error, so a
    missing count is reported as unknown rather than as zero.
    """
    url = f"https://{host}{TALEO_SEARCH_PATH.format(portal=portal)}"
    try:
        response = await client.post(
            url,
            json={"fieldData": {}, "multilineEnabled": False, "pageNo": 1},
            headers={
                "content-type": "application/json",
                "tz": "UTC",
                "tzname": "UTC",
                "referer": referer,
            },
            timeout=20.0,
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("careerSectionUnAvailable"):
        return None
    paging = data.get("pagingData")
    if isinstance(paging, dict):
        total = paging.get("totalCount")
        if isinstance(total, int):
            return total
    rows = data.get("requisitionList")
    return len(rows) if isinstance(rows, list) else None


async def _verify_taleo(
    client: httpx.AsyncClient, board: Board, sem: asyncio.Semaphore
) -> int | None:
    """Live Taleo section: its own job search, counted the way it counts.

    A retired section redirects to a generic Taleo page, so only an answer that
    stays on the tenant's own host counts.  Current sections render their
    listing client-side and name a portal id in the page; older ones still
    serve ``jobdetail.ftl`` links, which stay the fallback count.
    """
    host = urlsplit(board.url).netloc.lower()
    async with sem:
        await asyncio.sleep(0.05)
        for attempt in range(VERIFY_ATTEMPTS):
            try:
                response = await client.get(board.url, timeout=20.0, follow_redirects=True)
            except httpx.HTTPError:
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code == 200:
                if urlsplit(str(response.url)).netloc.lower() != host:
                    return None
                portal = TALEO_PORTAL_RE.search(response.text)
                if portal is not None:
                    total = await _taleo_postings_of(
                        client, host, portal.group(1), str(response.url)
                    )
                    if total is not None:
                        return total
                return len(set(TALEO_DETAIL_RE.findall(response.text)))
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            return None
    return _UNREACHABLE


async def _verify_personio(
    client: httpx.AsyncClient, board: Board, sem: asyncio.Semaphore
) -> int | None:
    """Posting count from a Personio board's own XML feed.

    A tenant that does not exist answers 404, and an unknown label on a
    customer host redirects to Personio's marketing site; both are dead.
    """
    host = urlsplit(board.url).netloc.lower()
    async with sem:
        await asyncio.sleep(0.05)
        for attempt in range(VERIFY_ATTEMPTS):
            await _pace(host)
            try:
                response = await client.get(
                    f"https://{host}/xml", timeout=20.0, follow_redirects=True
                )
            except httpx.HTTPError:
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code == 200:
                if urlsplit(str(response.url)).netloc.lower() != host:
                    return None
                # An error page can wear a 200; only the feed proves a board.
                if "workzag-jobs" not in response.text:
                    return None
                return response.text.count("<position>")
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt + 1 < VERIFY_ATTEMPTS:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            return None
    return _UNREACHABLE


async def _verify_breezy(
    client: httpx.AsyncClient, board: Board, sem: asyncio.Semaphore
) -> int | None:
    """Posting count from a Breezy board's public ``/json`` document."""
    host = urlsplit(board.url).netloc.lower()
    async with sem:
        await asyncio.sleep(0.05)
        for attempt in range(VERIFY_ATTEMPTS):
            await _pace(host)
            try:
                response = await client.get(
                    f"https://{host}/json", timeout=20.0, follow_redirects=True
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
                return len(data) if isinstance(data, list) else None
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
    if board.connector_id == "ats.oracle_recruiting":
        return await _verify_oracle(client, board, sem)
    if board.connector_id == "ats.jazzhr":
        return await _verify_jazzhr(client, board, sem)
    if board.connector_id == "ats.eightfold":
        return await _verify_eightfold(client, board, sem)
    if board.connector_id == "ats.taleo":
        return await _verify_taleo(client, board, sem)
    if board.connector_id == "ats.personio":
        return await _verify_personio(client, board, sem)
    if board.connector_id == "ats.breezy":
        return await _verify_breezy(client, board, sem)
    if board.connector_id == "ats.paylocity":
        return await _verify_paylocity(client, board, sem)
    template = API_TEMPLATES.get(board.connector_id)
    async with sem:
        await asyncio.sleep(0.05)
        if template:
            host = urlsplit(template).netloc.lower()
            for attempt in range(VERIFY_ATTEMPTS):
                await _pace(host)
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
    client: httpx.AsyncClient,
    boards: dict[Board, int],
    budget_s: float = 0.0,
    *,
    checkpoint: bool = True,
    already: Sequence[tuple[Board, int]] = (),
) -> tuple[list[tuple[Board, int]], dict[str, int]]:
    """Verify candidates, strongest first, for at most ``budget_s`` seconds.

    Returns the ``(board, posting count)`` pairs that are live, plus one
    outcome per settled board (posting count, or ``-1`` for a board the ATS
    does not know) so the next run can skip it.  A board the time budget
    leaves unvisited is settled neither way: it stays a candidate for the next
    run instead of being remembered as dead.

    Every ``VERIFY_CHECKPOINT_EVERY`` settled boards the outcomes are merged
    into the verified cache, so a run interrupted after hours of verifying
    keeps everything it settled instead of starting over.  ``checkpoint=False``
    is for ``--dry-run``, which promises not to write the cache.

    ``already`` are live results a previous run settled (see ``_resume_live``):
    they are kept in the returned list and skipped here, so an interruption
    does not cost a second verification of every live board.
    """
    sem = asyncio.Semaphore(VERIFY_CONCURRENCY)
    verified: list[tuple[Board, int]] = list(already)
    skip = {_cache_key(board) for board, _count in already}
    outcomes: dict[str, int] = {}
    live_by: dict[str, int] = defaultdict(int)
    dead_by: dict[str, int] = defaultdict(int)
    unreachable_by: dict[str, int] = defaultdict(int)
    done = 0
    started = time.monotonic()
    pending: dict[str, int] = {}
    stored: dict[str, dict[str, object]] | None = _read_verified() if checkpoint else None
    since_checkpoint = 0
    board_by_key = {_cache_key(board): board for board in boards}

    def flush() -> None:
        nonlocal pending, since_checkpoint
        if not checkpoint or not pending:
            return
        _checkpoint_verified(pending, stored, board_by_key)
        _log("verify_checkpoint", settled=len(pending), total_settled=len(outcomes))
        pending = {}
        since_checkpoint = len(outcomes)

    async def check(board: Board) -> None:
        nonlocal done
        if budget_s and time.monotonic() - started >= budget_s:
            count = _UNREACHABLE
        else:
            count = await verify_board(client, board, sem)
        done += 1
        if count is None:
            outcomes[_cache_key(board)] = -1
            pending[_cache_key(board)] = -1
            dead_by[board.connector_id] += 1
        elif count >= 0:
            verified.append((board, count))
            outcomes[_cache_key(board)] = count
            pending[_cache_key(board)] = count
            live_by[board.connector_id] += 1
        else:
            # Neither live nor known-dead: never remembered, always retried.
            unreachable_by[board.connector_id] += 1
        if len(outcomes) - since_checkpoint >= VERIFY_CHECKPOINT_EVERY:
            flush()
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

    # Highest-captured first: a board the archive kept meeting is likelier to
    # still be hiring than one captured once, so a budgeted run spends its
    # requests on the boards most likely to end up in the seed.
    ordered = sorted(
        (board for board in boards if _cache_key(board) not in skip),
        key=lambda board: boards[board],
        reverse=True,
    )
    # Launch in bounded batches: creating every task at once would evaluate the
    # time budget at the only instant it is still unspent, and a paced host
    # would then keep the run going for hours regardless of it.
    for start in range(0, len(ordered), VERIFY_BATCH):
        if budget_s and time.monotonic() - started >= budget_s:
            _log(
                "verify_budget_exhausted",
                visited=done,
                deferred=len(ordered) - done,
                budget_s=budget_s,
                elapsed_s=round(time.monotonic() - started),
            )
            break
        await asyncio.gather(*(check(board) for board in ordered[start : start + VERIFY_BATCH]))
    flush()
    _log(
        "verify_done",
        total=len(boards),
        resumed=len(already),
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


def _record_for(board: Board, jobs: int, verified_at: str) -> SeedRecord:
    """One shard record: the board, its observed posting count and the date.

    The count is what makes the ranking real: a board with openings sorts
    above a live-but-empty one, and an empty board keeps its dated zero instead
    of being dropped (it is future supply).
    """
    tenant = board.tenant
    if board.connector_id == "ats.ultipro" and any(char.isdigit() for char in tenant):
        # UKG Pro employers are addressed by opaque codes (``ACME1002``).
        tenant = ""
    return SeedRecord(
        connector_id=board.connector_id,
        url=board.url,
        postings=max(0, jobs),
        verified_at=verified_at,
        tenant=tenant or None,
        tenant_key=board.tenant_key,
    )


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


def _existing_records() -> list[SeedRecord]:
    """The seed as it is right now (shards, or the legacy single file)."""
    if (SEEDS_DIR / "manifest.json").exists():
        try:
            return read_all_records(SEEDS_DIR)
        except Exception as exc:  # noqa: BLE001 - a broken seed must be reported, not fatal
            _log("seed_shards_unreadable", error=type(exc).__name__, detail=str(exc))
            return []
    try:
        rows = json.loads(SEEDS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return records_from_legacy_rows([row for row in rows if isinstance(row, dict)])


def _board_from_seed(connector_id: str, url: str, tenant_key: str | None) -> Board | None:
    """Reconstruct a board from a stored row, for identity-based merging."""
    if connector_id == "ats.ultipro":
        return _ultipro_board(url)
    if connector_id == "ats.oracle_recruiting":
        return _oracle_board(url)
    if connector_id == "ats.paylocity":
        return _paylocity_board(url)
    if connector_id in SUBDOMAIN_ATS:
        board = _subdomain_board(connector_id, url)
        if board is None:
            return None
        return Board(board.connector_id, board.tenant, board.url, tenant_key or board.tenant_key)
    segments = [part for part in urlsplit(url).path.split("/") if part]
    if not segments:
        return None
    return Board(connector_id, segments[0], url, tenant_key)


def _merge_records(fresh: list[SeedRecord], existing: list[SeedRecord]) -> list[SeedRecord]:
    """Merge a fresh ranking into the shipped one by *board identity*.

    Keying on the URL would duplicate a Workday board seen through another
    locale/shard or a Greenhouse board seen on its sibling host; the same
    identity rule the runtime uses keeps one row per board.  A freshly
    verified row always replaces the old one.
    """
    merged: dict[tuple[str, ...], SeedRecord] = {}
    for record in existing:
        board = _board_from_seed(record.connector_id, record.url, record.tenant_key)
        key = board.dedupe_key if board is not None else (record.connector_id, record.url)
        merged[key] = record
    for record in fresh:
        board = _board_from_seed(record.connector_id, record.url, record.tenant_key)
        key = board.dedupe_key if board is not None else (record.connector_id, record.url)
        merged[key] = record
    return list(merged.values())


def _write(
    verified: list[tuple[Board, int]],
    limit: int,
    merge_existing: bool = False,
    *,
    min_postings: int = 0,
) -> None:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    ranked = sorted(verified, key=lambda item: item[1], reverse=True)
    if min_postings > 0:
        ranked = [(board, jobs) for board, jobs in ranked if jobs >= min_postings]
    if limit > 0:
        ranked = ranked[:limit]
    fresh = [_record_for(board, jobs, today) for board, jobs in ranked]
    records = _merge_records(fresh, _existing_records()) if merge_existing else fresh
    manifest = write_seed_shards(SEEDS_DIR, records, zero_unconfident=ZERO_UNCONFIDENT)
    by_connector: dict[str, int] = defaultdict(int)
    for board, _jobs in ranked:
        by_connector[board.connector_id] += 1
    _log(
        "write",
        path=str(SEEDS_DIR),
        rows=manifest.total,
        hiring=manifest.hiring_total,
        added=len(ranked),
        merged=merge_existing,
        by_connector=dict(sorted(by_connector.items())),
    )
    _log("top_boards", boards=[f"{board.url} ({jobs})" for board, jobs in ranked[:10]])


def _write_report(
    path: str,
    *,
    mode: str,
    candidates: dict[Board, int],
    verified: list[tuple[Board, int]],
    outcomes: dict[str, int],
    limit: int,
    min_postings: int,
) -> dict[str, object]:
    """Per-connector measurement of a run, written before anything is shipped."""
    by_connector: dict[str, dict[str, int]] = defaultdict(
        lambda: {"live": 0, "hiring": 0, "empty": 0, "dead": 0, "unreachable": 0}
    )
    for board, jobs in verified:
        entry = by_connector[board.connector_id]
        entry["live"] += 1
        entry["hiring" if jobs > 0 else "empty"] += 1
    live = {_cache_key(board) for board, _jobs in verified}
    for board in candidates:
        key = _cache_key(board)
        if key in live:
            continue
        if key in outcomes:
            if _entry_count(outcomes[key]) < 0:
                by_connector[board.connector_id]["dead"] += 1
        else:
            by_connector[board.connector_id]["unreachable"] += 1
    eligible = [jobs for _board, jobs in verified if jobs >= min_postings]
    report: dict[str, object] = {
        "mode": mode,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "candidates": len(candidates),
        "live": len(verified),
        "hiring": sum(1 for _board, jobs in verified if jobs > 0),
        "empty": sum(1 for _board, jobs in verified if jobs == 0),
        "dead": sum(1 for value in outcomes.values() if _entry_count(value) < 0),
        "unreachable": sum(1 for board in candidates if _cache_key(board) not in outcomes),
        "would_write": min(limit, len(eligible)) if limit > 0 else len(eligible),
        "min_postings": min_postings,
        "by_connector": {key: dict(value) for key, value in sorted(by_connector.items())},
    }
    if path:
        _atomic_write(Path(path), json.dumps(report, indent=2) + "\n")
    return report


def _load_candidates() -> dict[Board, int]:
    """Candidates from the shipped seed (used by ``--refresh``)."""
    boards: dict[Board, int] = {}
    for record in _existing_records():
        board = _board_from_seed(record.connector_id, record.url, record.tenant_key)
        if board is not None:
            boards[board] = record.postings
    return boards


def _cache_key(board: Board) -> str:
    """Stable identity of a board for the verified-outcome cache."""
    return "|".join(board.dedupe_key)


#: URL shapes whose board identity *is* the settled cache key, so a board the
#: cache settled as dead can be asked again without harvesting anything.  A
#: measured audit of the dead pool found a quarter of it live (a Workable
#: widget endpoint that 404'd during a bad window settled 10,000 boards dead),
#: and a settle-for-ever cache hid every one of them.
_KEY_BOARD_TEMPLATES: dict[str, str] = {
    "ats.greenhouse": "https://boards.greenhouse.io/{tenant}",
    "ats.lever": "https://jobs.lever.co/{tenant}",
    "ats.ashby": "https://jobs.ashbyhq.com/{tenant}",
    "ats.workable": "https://apply.workable.com/{tenant}",
    "ats.smartrecruiters": "https://jobs.smartrecruiters.com/{tenant}",
    "ats.bamboohr": "https://{tenant}.bamboohr.com",
    "ats.icims": "https://{tenant}.icims.com",
    "ats.jazzhr": "https://{tenant}.applytojob.com/apply",
    "ats.eightfold": "https://{tenant}.eightfold.ai/careers",
    "ats.paylocity": "https://recruiting.paylocity.com/recruiting/jobs/all/{tenant}",
}


def _dead_board(key: str) -> Board | None:
    """The board a settled cache key names, when the key determines its URL.

    Workday's identity drops the shard and UKG Pro's drops the employer code,
    so neither can be rebuilt from the key alone; those entries are left to a
    harvest, which is where their URL comes from in the first place.
    """
    parts = key.split("|")
    connector = parts[0] if parts else ""
    if connector == "ats.workday" or connector == "ats.ultipro":
        return None
    if connector == "ats.taleo" and len(parts) == 3:
        url = f"https://{parts[1]}.taleo.net/careersection/{parts[2]}"
        return _board_from_seed(connector, url, f"{parts[1]}|{parts[2]}")
    if connector == "ats.oracle_recruiting" and len(parts) == 3:
        url = f"https://{parts[1]}/hcmUI/CandidateExperience/en/sites/{parts[2]}"
        return _board_from_seed(connector, url, None)
    template = _KEY_BOARD_TEMPLATES.get(connector)
    if template is None or len(parts) != 2:
        return None
    return _board_from_seed(connector, template.format(tenant=parts[1]), None)


def _dead_candidates(
    checked: Mapping[str, object],
    only: set[str] | None = None,
) -> tuple[dict[Board, int], dict[str, int]]:
    """Boards the cache settled as dead, rebuilt so this run can ask again.

    Returns the candidates and the count of entries whose identity cannot name
    a URL, per connector, so the run log says what it could not reopen.
    ``only`` narrows the pool to named connectors: a measured error in one
    ATS's verifier should not spend an hour re-probing the other pools.
    """
    boards: dict[Board, int] = {}
    skipped: dict[str, int] = defaultdict(int)
    for key, value in checked.items():
        if _entry_count(value) >= 0:
            continue
        connector = key.split("|")[0]
        if only is not None and connector not in only:
            continue
        board = _dead_board(key)
        if board is None:
            skipped[connector] += 1
            continue
        boards[board] = 1
    return boards, dict(sorted(skipped.items()))


def _read_verified() -> dict[str, dict[str, object]]:
    """Settled boards: ``{identity: {"count": n, "at": ISO}}``.

    Older runs wrote a bare integer per board.  Those entries are stamped with
    the cache file's mtime rather than being trusted forever: otherwise a dead
    board settled by an old run would never be re-checked, silently disabling
    ``--recheck-dead-after-days``.  The mtime is when the evidence was last
    written, which is the best timestamp a legacy entry has.
    """
    try:
        raw = json.loads(VERIFIED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    legacy_at = ""
    if any(isinstance(value, int) for value in raw.values()):
        try:
            legacy_at = datetime.fromtimestamp(VERIFIED_PATH.stat().st_mtime, UTC).isoformat(
                timespec="seconds"
            )
        except OSError:
            legacy_at = datetime.now(UTC).isoformat(timespec="seconds")
    entries: dict[str, dict[str, object]] = {}
    for key, value in raw.items():
        if isinstance(value, int):
            entries[str(key)] = {"count": value, "at": legacy_at}
        elif isinstance(value, dict) and isinstance(value.get("count"), int):
            entry: dict[str, object] = {
                "count": value["count"],
                "at": str(value.get("at") or ""),
            }
            # Enough to rebuild the Board, so an interrupted run can write the
            # live boards it already verified instead of verifying them again.
            for field in ("url", "tenant", "connector", "tenant_key"):
                if isinstance(value.get(field), str):
                    entry[field] = value[field]
            entries[str(key)] = entry
    return entries


def _entry_board(value: object) -> Board | None:
    """The board a settled live entry describes, when the entry carries one."""
    if not isinstance(value, dict):
        return None
    connector = value.get("connector")
    url = value.get("url")
    tenant = value.get("tenant")
    tenant_key = value.get("tenant_key")
    if not all(isinstance(item, str) and item for item in (connector, url, tenant)):
        return None
    return Board(
        str(connector),
        str(tenant),
        str(url),
        str(tenant_key) if isinstance(tenant_key, str) and tenant_key else None,
    )


def _resume_live(
    checked: Mapping[str, object],
    *,
    days: int = RESUME_LIVE_DAYS,
    now: datetime | None = None,
) -> list[tuple[Board, int]]:
    """Live boards a previous (possibly interrupted) run already verified.

    Only fresh entries are reused: their posting count is written to the
    shards exactly as if this run had probed the board.  Dead entries are not
    included -- ``_new_only`` handles them with the re-check window.
    """
    moment = now or datetime.now(UTC)
    resumed: list[tuple[Board, int]] = []
    for value in checked.values():
        count = _entry_count(value)
        if count < 0:
            continue
        board = _entry_board(value)
        if board is None:
            continue
        at = _entry_at(value)
        if days > 0 and at:
            try:
                settled = datetime.fromisoformat(at.replace("Z", "+00:00"))
            except ValueError:
                settled = None
            if settled is not None:
                if settled.tzinfo is None:
                    settled = settled.replace(tzinfo=UTC)
                if (moment - settled).days >= days:
                    continue
        resumed.append((board, count))
    return resumed


def _entry_count(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, dict) and isinstance(value.get("count"), int):
        return int(value["count"])  # type: ignore[arg-type]
    return 0


def _entry_at(value: object) -> str:
    if isinstance(value, dict):
        raw = value.get("at")
        return raw if isinstance(raw, str) else ""
    return ""


def _dead_is_current(value: object, *, days: int, now: datetime) -> bool:
    """Whether a settled-dead board is still inside its re-check window."""
    if days <= 0:
        return False
    at = _entry_at(value)
    if not at:
        return True  # legacy entry without a timestamp: trust it once more
    try:
        moment = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (now - moment).days < days


def _checkpoint_verified(
    outcomes: dict[str, int],
    stored: dict[str, dict[str, object]] | None = None,
    boards: Mapping[str, Board] | None = None,
) -> None:
    """Remember what this run settled, and when, so the next one can skip it.

    ``stored`` lets a long verification pass keep the cache in memory and only
    pay for serialisation, instead of re-reading a multi-megabyte file for
    every checkpoint.  ``boards`` adds enough identity to rebuild a live board
    on resume, so an interrupted run does not have to verify it again.
    """
    if not outcomes:
        return
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    entries = _read_verified() if stored is None else stored
    for key, count in outcomes.items():
        entry: dict[str, object] = {"count": count, "at": stamp}
        board = boards.get(key) if boards is not None else None
        if board is not None:
            entry["url"] = board.url
            entry["tenant"] = board.tenant
            entry["connector"] = board.connector_id
            if board.tenant_key:
                entry["tenant_key"] = board.tenant_key
        entries[key] = entry
    _atomic_write(VERIFIED_PATH, json.dumps(entries, indent=1))


def _by_connector(boards: dict[Board, int]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for board in boards:
        counts[board.connector_id] += 1
    return dict(sorted(counts.items()))


def _new_only(
    candidates: dict[Board, int],
    shipped: dict[Board, int],
    checked: Mapping[str, object] | None = None,
    *,
    recheck_dead_days: int = RECHECK_DEAD_DAYS,
    now: datetime | None = None,
) -> dict[Board, int]:
    """The candidates the shipped seed does not already cover.

    Keyed on ``Board.dedupe_key``, not URL, so another locale or shard of a
    board already in the file is neither re-verified nor re-added.  A board a
    previous run settled as *dead* is skipped while its evidence is current;
    after ``recheck_dead_days`` it is asked again, because a retired tenant can
    reopen.  A board settled as live is never skipped (a live board missing
    from the file has to be put back).
    """
    present = {_cache_key(board) for board in shipped}
    moment = now or datetime.now(UTC)
    dead = {
        key
        for key, value in (checked or {}).items()
        if _entry_count(value) < 0 and _dead_is_current(value, days=recheck_dead_days, now=moment)
    }
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
        "--refresh-connectors",
        default="",
        help=(
            "comma-separated connectors --refresh should re-verify, e.g. "
            "ats.taleo; their rows are merged back instead of replacing the seed"
        ),
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
        default=6,
        help="10,000-row CDX chunks to walk for BambooHR (0 disables)",
    )
    parser.add_argument(
        "--ultipro-chunks",
        type=int,
        default=24,
        help="10,000-row CDX chunks to walk for UKG Pro/UltiPro (0 disables)",
    )
    parser.add_argument(
        "--eightfold-chunks",
        type=int,
        default=12,
        help="10,000-row CDX chunks to walk for Eightfold (0 disables)",
    )
    parser.add_argument(
        "--taleo-chunks",
        type=int,
        default=6,
        help="10,000-row CDX chunks to walk for Taleo (0 disables)",
    )
    parser.add_argument(
        "--workday-buckets",
        action="store_true",
        help=(
            "walk Workday by shard and tenant initial (with bucket splitting) "
            "instead of by filtered chunk"
        ),
    )
    parser.add_argument(
        "--bucket-sweep",
        default="",
        help=(
            "comma-separated subdomain ATSs to enumerate one tenant initial at a "
            "time, e.g. ats.bamboohr (no pagination, so it cannot stall)"
        ),
    )
    parser.add_argument(
        "--verify-budget-s",
        type=float,
        default=0.0,
        help="seconds to spend verifying (0 verifies every candidate)",
    )
    parser.add_argument(
        "--jazzhr-chunks",
        type=int,
        default=12,
        help="10,000-row CDX chunks to walk for JazzHR (0 disables)",
    )
    parser.add_argument(
        "--personio-chunks",
        type=int,
        default=0,
        help="10,000-row CDX chunks to walk for Personio (0 disables)",
    )
    parser.add_argument(
        "--breezy-chunks",
        type=int,
        default=0,
        help="10,000-row CDX chunks to walk for Breezy HR (0 disables)",
    )
    parser.add_argument(
        "--rippling-chunks",
        type=int,
        default=0,
        help="archive prefix queries to spend on Rippling's slug slices (0 disables)",
    )
    parser.add_argument(
        "--paylocity-chunks",
        type=int,
        default=0,
        help="archive prefix queries to spend on Paylocity's board slices (0 disables)",
    )
    parser.add_argument(
        "--oracle-chunks",
        type=int,
        default=0,
        help=(
            "10,000-row CDX chunks to walk across Oracle's HCM pods "
            "(0 disables); each pod keeps its own resume key"
        ),
    )
    parser.add_argument(
        "--bucket-budget-s",
        type=float,
        default=BUCKET_BUDGET_S,
        help=(
            "seconds a bucket sweep may spend probing the archive before it "
            "stops and writes what it has (default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--min-postings",
        type=int,
        default=0,
        help="omit live boards below this posting count (0 keeps every live board)",
    )
    parser.add_argument(
        "--recheck-dead",
        action="store_true",
        help=(
            "ask the boards settled dead again, rebuilt from their cached "
            "identity (needs --incremental; pairs with --recheck-dead-after-days 0)"
        ),
    )
    parser.add_argument(
        "--recheck-dead-connectors",
        default="",
        help=(
            "comma-separated connectors to reopen, e.g. ats.workable; "
            "the rest of the dead pool stays settled"
        ),
    )
    parser.add_argument(
        "--recheck-dead-after-days",
        type=int,
        default=RECHECK_DEAD_DAYS,
        help=(
            "re-verify a board settled as dead after this many days "
            "(0 always rechecks; default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="verify and report without writing shards or the verified cache",
    )
    parser.add_argument(
        "--report",
        default="",
        help="write the per-connector run report here (JSON)",
    )
    args = parser.parse_args()
    if args.refresh and args.incremental:
        parser.error("--refresh and --incremental are mutually exclusive")
    if args.recheck_dead and not args.incremental:
        parser.error("--recheck-dead needs --incremental: the settled cache is its input")
    if args.recheck_dead_connectors and not args.recheck_dead:
        parser.error("--recheck-dead-connectors only narrows --recheck-dead")
    if args.refresh_connectors and not args.refresh:
        parser.error("--refresh-connectors only narrows --refresh")

    LOG_PATH = Path(args.log_file)
    checked: dict[str, dict[str, object]] = _read_verified() if args.incremental else {}
    # A previous run may have verified thousands of live boards before it was
    # interrupted; reuse those results instead of probing them again.
    resumed = _resume_live(checked) if args.incremental else []
    resumed_keys = {_cache_key(board) for board, _count in resumed}
    if resumed:
        _log(
            "resume_live",
            boards=len(resumed),
            by_connector=_by_connector({board: 1 for board, _count in resumed}),
        )
    mode = "refresh" if args.refresh else "incremental" if args.incremental else "harvest"
    _log("run_start", mode=mode, argv=" ".join(sys.argv[1:]))

    async with httpx.AsyncClient(
        headers={"User-Agent": "Lodestar seed builder"},
        follow_redirects=True,
    ) as client:
        if args.refresh:
            candidates = _load_candidates()
            if args.refresh_connectors:
                # Re-measuring one connector (after its posting counter was
                # repaired) must not re-probe -- or replace -- the other 50,000
                # boards, so the rest of the shipped seed is carried over.
                wanted = {
                    item.strip() for item in args.refresh_connectors.split(",") if item.strip()
                }
                candidates = {
                    board: count
                    for board, count in candidates.items()
                    if board.connector_id in wanted
                }
                _log(
                    "refresh_connectors",
                    requested=sorted(wanted),
                    matched=len(candidates),
                    by_connector=_by_connector(candidates),
                )
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
                or bool(args.bucket_sweep)
                or args.oracle_chunks > 0
                or args.jazzhr_chunks > 0
                or args.personio_chunks > 0
                or args.breezy_chunks > 0
                or args.rippling_chunks > 0
                or args.eightfold_chunks > 0
                or args.taleo_chunks > 0
                or args.workday_buckets
                or args.paylocity_chunks > 0
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
                fixed = await harvest_all(client)
            _log("fixed_done", candidates=len(fixed), by_connector=_by_connector(fixed))
            subdomains: dict[Board, int] = {}
            for connector_id, chunks in (
                ("ats.workday", args.workday_chunks),
                ("ats.icims", args.icims_chunks),
                ("ats.bamboohr", args.bamboohr_chunks),
                ("ats.oracle_recruiting", args.oracle_chunks),
                ("ats.jazzhr", args.jazzhr_chunks),
                ("ats.personio", args.personio_chunks),
                ("ats.breezy", args.breezy_chunks),
                ("ats.eightfold", args.eightfold_chunks),
                ("ats.taleo", args.taleo_chunks),
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
            for swept_id in (item.strip() for item in args.bucket_sweep.split(",")):
                if not swept_id:
                    continue
                if swept_id not in SUBDOMAIN_ATS:
                    _log("bucket_sweep_skipped", connector=swept_id, reason="not a subdomain ATS")
                    continue
                swept = await harvest_initial_buckets(
                    client, swept_id, args.resume, args.bucket_budget_s
                )
                _log("bucket_sweep_candidates", connector=swept_id, candidates=len(swept))
                subdomains.update(swept)
            if args.workday_buckets:
                shard_swept = await harvest_shard_buckets(
                    client, "ats.workday", args.bucket_budget_s
                )
                _log(
                    "shard_bucket_candidates",
                    connector="ats.workday",
                    candidates=len(shard_swept),
                )
                subdomains.update(shard_swept)
            if args.ultipro_chunks > 0:
                ultipro = await harvest_path_boards(
                    client, "ats.ultipro", args.ultipro_chunks, args.resume
                )
                _log("subdomain_candidates", connector="ats.ultipro", candidates=len(ultipro))
                subdomains.update(ultipro)
            if args.paylocity_chunks > 0:
                paylocity = await harvest_prefix_boards(
                    client, "ats.paylocity", args.paylocity_chunks, args.resume
                )
                _log("subdomain_candidates", connector="ats.paylocity", candidates=len(paylocity))
                subdomains.update(paylocity)
            if args.rippling_chunks > 0:
                rippling = await harvest_prefix_boards(
                    client, "ats.rippling", args.rippling_chunks, args.resume
                )
                _log("subdomain_candidates", connector="ats.rippling", candidates=len(rippling))
                subdomains.update(rippling)
            shipped = _load_candidates()
            candidates = _merge(shipped, fixed, subdomains)
            if args.recheck_dead:
                # A board settled dead is skipped by ``_new_only`` while its
                # evidence is current, so reopening the pool has to happen
                # before that filter: rebuild the boards from the cache keys
                # and let the same re-check window decide.
                wanted = {
                    item.strip() for item in args.recheck_dead_connectors.split(",") if item.strip()
                }
                reopened, unrebuildable = _dead_candidates(checked, wanted or None)
                candidates = _merge(candidates, reopened)
                _log(
                    "dead_recheck_candidates",
                    candidates=len(reopened),
                    unrebuildable=unrebuildable,
                )
            _log(
                "candidates",
                total=len(candidates),
                shipped=len(shipped),
                by_connector=_by_connector(candidates),
            )
            if args.incremental:
                candidates = {
                    board: count
                    for board, count in candidates.items()
                    if _cache_key(board) not in resumed_keys
                }
                candidates = _new_only(
                    candidates,
                    shipped,
                    checked,
                    recheck_dead_days=args.recheck_dead_after_days,
                )
                _log(
                    "incremental_filter",
                    candidates=len(candidates),
                    skipped=len(checked),
                    resumed=len(resumed),
                    by_connector=_by_connector(candidates),
                )

        if not candidates and not resumed:
            _log("no_candidates")
            return

        _log("verify_start", candidates=len(candidates), by_connector=_by_connector(candidates))
        verified, outcomes = await verify_all(
            client,
            candidates,
            args.verify_budget_s,
            checkpoint=not args.dry_run,
            already=resumed,
        )

    report = _write_report(
        args.report,
        mode=mode,
        candidates=candidates,
        verified=verified,
        outcomes=outcomes,
        limit=args.limit,
        min_postings=args.min_postings,
    )
    _log(
        "run_report",
        live=report["live"],
        hiring=report["hiring"],
        empty=report["empty"],
        dead=report["dead"],
        unreachable=report["unreachable"],
        would_write=report["would_write"],
    )
    if args.dry_run:
        _log("dry_run_end", mode=mode, verified=len(verified), report=args.report or None)
        return
    checkpoint_boards = {_cache_key(board): board for board in candidates}
    checkpoint_boards.update({_cache_key(board): board for board, _count in resumed})
    _checkpoint_verified(outcomes, None, checkpoint_boards)

    _write(
        verified,
        args.limit,
        merge_existing=args.incremental or bool(args.refresh_connectors),
        min_postings=args.min_postings,
    )
    _log("run_end", mode=mode, verified=len(verified))


if __name__ == "__main__":
    asyncio.run(main())
