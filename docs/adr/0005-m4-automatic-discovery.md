# ADR 0005 — Milestone 4 automatic discovery

- Status: accepted
- Date: 2026-11-18
- Scope: Milestone 4 (automatic discovery and daily scheduling)

## Context

The product promise is that **jobs find the user**, not that the user searches
for jobs. Milestone 4 therefore runs discovery without user input.

The first implementation of the milestone shipped one provider: a bundled JSON
seed list of eight public ATS tenants. That is not discovery. Companies appear
and disappear, ATS tenants are renamed and retired, and a data file frozen at
release time describes the job market of the past. It also put the burden back
on the user, who would have to add sources whenever the list missed an employer.

## Decision

1. **Add `discovery.web_index`**, which enumerates ATS tenants from public web
   URL indexes with no API key and no user input. **Common Crawl's index
   server is primary** (prefix queries answer in seconds; each query covers
   one `(ATS host, leading-character bucket)` pair and a pass walks several
   of them, newest crawl first with one older crawl as fallback). When Common
   Crawl cannot serve a slice — during evaluation its gateway consistently
   timed out on the largest ATS host (`boards.greenhouse.io`, both 1- and
   2-character prefixes) — the provider falls back to the **Internet Archive
   CDX API**. CDX is queried with a **high row limit** (`CDX_LIMIT`, 10,000,
   configurable as `discovery.web_index_cdx_limit`): measured, `limit=10000`
   answers in ~13 s and returns 10,001 rows for
   `boards.greenhouse.io/a`, which reduce to 102 distinct tenants. The
   original 60-row cap sampled a fraction of a percent of a bucket and was the
   reason an early seed list came out undersized. The query omits the
   expensive `filter`/`from` clauses and carries a 60 s request timeout; a
   failed CDX call is retried once while the pass still has wall-clock budget.
   CDX is the scarce resource: a pass spends at most four fallback queries
   (`MAX_CDX_FALLBACKS`) no matter how many slices come back empty, and the
   unserved slices are revisited when the rotation cursor wraps. Two more
   bounds keep a pass inside its discovery call budget: the `collinfo`
   listing is fetched **once per pass and shared by every slice** (not once
   per slice), and the provider stops starting expensive fallbacks once its
   wall-clock budget (`PASS_BUDGET_S`) is spent, yielding what earlier
   slices already collected. The provider
   reduces results to tenant board roots, ignores site chrome (`/search`,
   `/jobs`, `/embed`, assets), and dedupes `(connector id, tenant key)` within
   a pass, because the same tenant is often indexed on two Greenhouse hosts.
2. **Rotate instead of paging.** One pass walks a bounded set of slices
   (`discovery.cursor_step`, default 12), queried in parallel, starting at the
   current cursor. The pass stores an integer cursor in `app_settings` and
   advances it by the step, so each daily run explores new ground and the
   space is revisited after the cursor wraps. This avoids fragile
   pagination/resume tokens and bounds the work of a pass. If every index
   backend fails for every slice, the provider raises and the pass **holds
   the cursor**, so the same slice range is retried next pass instead of
   being skipped forever.
3. **Demote the seed file to a bootstrap.** `discovery.ats_directory` stays for
   cold start, demos and tests, and its description says so. `discovery.
   auto_discover` refuses to treat it as the discovery engine. As shipped, the
   seed file is a **warm start**: 25,716 boards across nine ATSs — the five
   fixed-host ATSs whose boards live at `host/{tenant}` (Greenhouse, Lever,
   Ashby, SmartRecruiters and Workable), the subdomain-addressed Workday, iCIMS
   and BambooHR boards, and path-addressed UKG Pro/UltiPro boards — each
   confirmed live by asking the board's own public ATS API (HTTP 200, including
   live boards that currently list no postings) and written **largest-first by
   open postings**, because the
   cold-start warm-up registers at most `discovery.max_new_sources_per_run`
   per pass, so ordering decides which employers are reached first. It is
   regenerated via `scripts/build_seed.py` (`--refresh` re-verifies and
   re-ranks without re-harvesting), which walks the bulk URL indexes in
   filtered 10,000-row slices, re-walks a full slice as narrower buckets, and
   checkpoints progress per slice, so a cold install is useful within minutes
   while the web-index rotation fills in long-tail companies. The file remains
   a bootstrap — discovery is the engine — and deleting it does not stop
   discovery.
4. **Bound every pass.** `discovery.max_new_sources_per_run` caps registration
   (registering a source is cheap; a dead board costs one failed crawl later),
   `discovery.max_crawl_sources_per_run` caps crawling, and crawling selects
   only sources that are *due* (never crawled, or own interval elapsed),
   stalest attempt first, run in parallel. One shared semaphore
   (`crawl.global_concurrency`) bounds concurrent crawls across schedules and
   the pass alike.
5. **Verify by crawling, not by trusting the index.** A tenant that disappeared
   costs one `crawl.failed` run and is never re-registered; the pass counts it
   as a failure instead of aborting.

## Options considered

- **Search-engine scraping** (`discovery.search_engine`): the endpoint blocked
  automated queries during evaluation (challenge pages) and result markup
  changes often. It stays an optional provider behind
  `features.search_engine_discovery`.
- **ATS-native directory APIs**: no supported ATS publishes a complete tenant
  directory; SmartRecruiters' company listing endpoint is gone, Greenhouse,
  Lever and Ashby publish per-tenant endpoints only.
- **Common Crawl index only**: rejected as the sole backend — its gateway
  returned 504s for the largest ATS host during evaluation, which would have
  made discovery for that host permanently blind. The CDX fallback covers it.
- **Internet Archive CDX only**: rejected as the sole backend — prefix scans
  measure 26–49 s per query at a 60-row limit and the gateway intermittently
  503s under load, while a 10,000-row query takes ~13 s but is still the slow
  backend. Common Crawl answers the same slices in seconds.
- **Domain queries for subdomain-based ATSs (Workday, iCIMS)**: adopted for the
  *seed bootstrap*, deferred for live discovery. The tenant is a subdomain
  there, so a `host/bucket` prefix query cannot reach it. CDX `matchType=domain`
  has no offset — the answer starts at the alphabetical head of the domain —
  but its `showResumeKey` walks the domain page by page, and a negative
  `filter=original` clause removes what otherwise fills the first chunks
  (Workday tenants own tens of thousands of `/assets/…` URLs; iCIMS' `www`
  marketing site dominates its head). Positive `filter=urlkey…`/`filter=
  original:^https?://…` narrowing returned 503/504 under load. The seed builder
  uses this path with checkpointed chunks; `discovery.web_index` still uses the
  prefix rotation, so new subdomain tenants wait for a seed refresh.
- **Static seed list only**: rejected — it cannot track a changing market and
  shifts work back onto the user.

## Consequences

- Discovery works on a fresh install with no user action and no seed data.
- The pass depends on third-party archives: if both are unreachable, that pass
  registers nothing new (logged as `discovery.provider_failed`) and the rest of
  the app keeps working; the cursor is held, so the next daily pass retries the
  same slice.
- Index lag means a brand-new company can take weeks to surface; coverage is
  therefore stated as limited in the UI and in `docs/how-discovery-works.md`.
- Source lists grow over time and include dead boards. The due-based crawl and
  the two budgets keep a pass bounded; dead boards are visible as failed crawl
  runs rather than being silently dropped.
- The seed file now also carries Workday and iCIMS boards. Workday rows persist
  their `tenant_key`, because the CxS endpoint needs the tenant and the board
  URL alone parses as `{locale}/{site}`; the discovery → registration path
  (`DiscoveredSource.tenant_key`) passes it into `SourceConfig.ats_tenant_key`.
- `crawl_runs.last_started_by_source()` was added so due selection does not
  need a new table or a schema migration.

## Addendum (2026-11-21): live domain rotation

The decision above deferred live enumeration of subdomain ATSs. That gap was
real: a new Workday, iCIMS or UKG Pro tenant appeared only after regenerating
the seed file. `discovery.domain_index` now closes it without depending on the
seed:

- **Domain mode** (`matchType=domain` + a leading-character `filter`) covers
  Workday (`{tenant}.wd{N}.myworkdayjobs.com/{locale}/{site}`), iCIMS and
  BambooHR. **Prefix mode** covers the fixed hosts `recruiting.ultipro.com` and
  `apply.workable.com`. Each captured URL is parsed back into a valid board;
  Workday's `{locale}/{site}` comes from the URL because the bare host answers
  406.
- **The cost is real.** Domain slices measured ~42 s (Workday) and ~50 s
  (BambooHR); an iCIMS slice returned a 504 at 60 s. A pass therefore walks
  three slices concurrently, with an 85 s timeout and **one** attempt, and logs
  a failed slice for the next wrap. Unlike `discovery.web_index`, it never
  holds the global cursor: a throttled archive must not stall the other
  providers. The provider yields nothing instead of raising.
- **Dedupe and noise.** Indexed asset/API paths (`/favicon.ico`,
  `/wday/cxs/...`) are rejected, and Workday's `/{site}` and `/{locale}/{site}`
  forms collapse to one board.
- **Oracle Recruiting Cloud** uses its own pod rotation
  (`discovery.oracle_recruiting`). Its tenants are subdomains of `fa.*` HCM
  pods, the pods use wildcard certificates (so CT cannot enumerate tenants),
  and a whole-domain query returns 504 — but a pod-scoped query answers in
  17–38 s. Two pods are walked per pass, and the board is reconstructed from
  the returned career-site URL.

The seed builder's domain-chunk path remains for the warm start; it is no
longer the only route to these tenants.
