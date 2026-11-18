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
   server is primary** (prefix queries answer in seconds; one run asks for a
   single `(ATS host, leading-character bucket)` pair, newest crawl first with
   one older crawl as fallback). When Common Crawl cannot serve a slice —
   during evaluation its gateway consistently timed out on the largest ATS
   host (`boards.greenhouse.io`, both 1- and 2-character prefixes) — the
   provider falls back to the **Internet Archive CDX API** (measured 26–49 s
   per prefix query; the query therefore omits the expensive `filter`/`from`
   clauses and carries a 90 s request timeout under a 120 s discovery call
   budget). The provider reduces results to tenant board roots and ignores
   site chrome (`/search`, `/jobs`, `/embed`, assets).
2. **Rotate instead of paging.** One run asks for a single
   `(ATS host, leading-character bucket)` pair. The pass stores an integer
   cursor in `app_settings` and advances it, so each daily run explores new
   ground and the space is revisited after the cursor wraps. This avoids
   fragile pagination/resume tokens and bounds the work of a pass. If every
   index backend fails for a slice, the provider raises and the pass **holds
   the cursor**, so the same slice is retried next pass instead of being
   skipped forever.
3. **Demote the seed file to a bootstrap.** `discovery.ats_directory` stays for
   cold start, demos and tests, and its description says so. `discovery.
   auto_discover` refuses to treat it as the discovery engine.
4. **Bound every pass.** `discovery.max_new_sources_per_run` caps registration,
   `discovery.max_crawl_sources_per_run` caps crawling, and crawling selects
   only sources that are *due* (never crawled, or own interval elapsed), oldest
   attempt first. One shared semaphore (`crawl.global_concurrency`) bounds
   concurrent crawls across schedules and the pass alike.
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
- **Internet Archive CDX only**: rejected as the sole backend — its prefix
  scans measure 26–49 s per query, at or beyond the request and plugin-call
  budgets, and the gateway intermittently 504s under load. Common Crawl
  answers the same slices in seconds.
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
- `crawl_runs.last_started_by_source()` was added so due selection does not
  need a new table or a schema migration.
