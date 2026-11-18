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

1. **Add `discovery.web_index`**, which enumerates ATS tenants from a public web
   URL index (the Internet Archive's CDX API) with no API key and no user
   input. The index lists URLs a crawler actually fetched, so it tracks live
   companies instead of a snapshot. Queries are prefix lookups on a known ATS
   host (`boards.greenhouse.io/s`, `jobs.ashbyhq.com/s`, ...); the provider
   reduces results to tenant board roots and ignores site chrome (`/search`,
   `/jobs`, `/embed`, assets).
2. **Rotate instead of paging.** One run asks for a single
   `(ATS host, leading-character bucket)` pair. The pass stores an integer
   cursor in `app_settings` and advances it, so each daily run explores new
   ground and the space is revisited after the cursor wraps. This avoids
   fragile pagination/resume tokens and bounds the work of a pass.
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
- **Common Crawl index**: prefix queries returned 504s repeatedly during
  evaluation; the Internet Archive CDX API answered the same queries.
- **Static seed list only**: rejected — it cannot track a changing market and
  shifts work back onto the user.

## Consequences

- Discovery works on a fresh install with no user action and no seed data.
- The pass depends on a third-party archive: if it is unreachable, that pass
  registers nothing new (logged as `discovery.provider_failed`) and the rest of
  the app keeps working; the next daily pass retries a different slice.
- Index lag means a brand-new company can take weeks to surface; coverage is
  therefore stated as limited in the UI and in `docs/how-discovery-works.md`.
- Source lists grow over time and include dead boards. The due-based crawl and
  the two budgets keep a pass bounded; dead boards are visible as failed crawl
  runs rather than being silently dropped.
- `crawl_runs.last_started_by_source()` was added so due selection does not
  need a new table or a schema migration.
