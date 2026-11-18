# ADR 0006: Relevance-ranked discovery funnel (tiered crawling over breadth-first crawling)

- **Status:** Accepted (design; implementation is Milestone 6)
- **Date:** 2026-11-17
- **Related:** [ADR 0005](0005-m4-automatic-discovery.md) (board-level discovery), spec §6.8, §19.10

## Context

Board-first discovery (M4) answers *"which companies are hiring?"* by walking
the ATS tenant space. But the US economy adds on the order of 165k net jobs
per month, spread across tens of thousands of boards. Two problems with
crawling our way through all of it:

1. **Cost scales with breadth, not relevance.** A full-depth crawl of every
   discovered board spends the budget roughly uniformly, while the postings a
   given user could plausibly get are a tiny, concentrated slice.
2. **Freshness is bounded by crawl cadence.** "Apply the moment it comes out"
   means a newly posted job on a relevant board must surface within an hour —
   impossible if every board on earth is polled daily at equal priority.

Meanwhile the same public URL indexes that give us tenants (Common Crawl,
Internet Archive CDX) also list **posting URLs** (`…/jobs/<id>`) — a
company's newest postings show up there days before we would ever schedule
that board for a full crawl, and long-tail companies may never need a full
crawl at all.

## Decision

1. **Harvest posting URLs from the indexes, not just tenants.** A funnel pass
   queries the same index slices for posting-path URLs and turns each into a
   candidate posting, verified with a **single fetch + extraction pass**
   instead of a board crawl. Provenance marks these "via index" until a full
   listing confirms them.
2. **Tier sources by relevance to the user.** The M5 candidate profile and M3
   hard filters rank sources into hot (every pass), warm (daily), and cold
   (weekly or index-only) tiers; posting-intelligence signals (M8) raise and
   lower tiers as they arrive. Tier changes are explainable and evented.
3. **Budgets are the boundary, not the lever.** The funnel never raises
   `max_new_sources_per_run`, `max_crawl_sources_per_run`, or the global
   crawl gate. Tiering only decides *which due sources* are picked and *how
   often each tier is crawled*. Index harvests add posting candidates at
   near-zero crawl cost, bounded by `discovery.funnel_postings_per_pass`.
4. **Full listings stay the source of truth** for `not_seen`/removal logic:
   a harvested-only posting is never the sole basis for marking another
   posting `removed`, and identity resolution (M3) dedupes it against the
   same company's full listing.

## Options considered

- **Crawl everything, faster** (raise caps, more concurrency): rejected —
  cost grows linearly with the whole economy, not with the user's relevant
  slice; also hostile to the boards we hammer.
- **Search-engine API for fresh postings**: rejected as primary (same
  challenge/ToS problems as the discovery provider evaluation, ADR 0005);
  remains possible as an optional user-enabled provider.
- **Re-crawl boards in posting-recency order only**: helps freshness but
  keeps uniform breadth cost; superseded by tiering, which it becomes for
  the hot tier.

## Consequences

- Coverage decouples from crawl load: thousands of indexed long-tail
  companies contribute postings without proportional crawling.
- The hot tier delivers the "apply the moment it appears" SLA for the most
  relevant boards; the index path covers the tail at days-of-freshness.
- Tier assignment depends on M5/M3, which is why this is Milestone 6, not an
  M4 extension.
- A web index's freshness lag (days) is an honest limit of this design; the
  spec (§6.8) documents it rather than promising real-time coverage
  everywhere.
