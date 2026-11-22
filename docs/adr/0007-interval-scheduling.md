# ADR 0007: Persisted interval schedules, not cron expressions

- **Status:** Accepted (implemented)
- **Date:** 2026-11-21
- **Related:** spec §6 / §20 (scheduling), `app/application/orchestration/scheduler.py`,
  ADR 0005 (automatic discovery), ADR 0006 (tiered cadence)

## Context

The M4 roadmap asks for "persistent per-source scheduler with cron/interval
schedules that survives worker restarts". The shipped scheduler is
interval-based:

- `SourceConfig.schedule` stores `interval_s` (and, after M6, `tier` and
  `tier_reason`) in SQLite;
- on worker start, `DiscoveryWiring.sync_scheduler` reloads enabled sources and
  rebuilds one background loop per source;
- the effective interval is the tighter of the source's own interval and its
  relevance tier (`effective_crawl_interval_s`);
- a settings change or a source change resyncs the loops without a restart;
- `discovery` also has its own pass cadence (`auto_discover_interval_minutes`,
  default 60) plus per-pass registration/crawl caps and a global crawl gate.

What is missing is cron-expression syntax (for example "weekdays at 07:00").
Nothing in the desktop product needs wall-clock calendars: users care that a
hot board is re-checked hourly and a cold board weekly, not that a crawl starts
at a particular minute. Adding a cron parser would expand the config surface,
create timezone/DST questions, and duplicate the tier logic that already
expresses relative cadence.

## Decision

1. **Ship interval schedules as the M4/M6 contract.** Intervals are persisted
   per source, restart-safe, tier-aware and bounded by the pass caps and global
   gate. This satisfies the observable requirement ("coverage survives a
   restart; nothing user-driven is required").
2. **Do not accept cron expressions in configuration.** `ScheduleConfig`
   carries `interval_hours` and `jitter_pct`; a source carries `interval_s`.
   Strict config validation rejects unknown keys, so a cron string fails loudly
   rather than being silently ignored.
3. **Jitter stays a cadence concern, not a cron concern.** The retry/backoff and
   per-origin rate limiter already prevent synchronized bursts; a future
   jitter pass can be added to the interval resolver without changing stored
   schedules.
4. **Revisit only with a user-facing calendar requirement** (for example
   "crawl overnight only"). If that arrives, add an optional cron field next to
   `interval_s`, keep interval as the default, and document timezone semantics
   in the spec first.

## Consequences

- The scheduling surface stays small and fully typed.
- Restart behaviour is testable without a clock daemon: reload sources, rebuild
  loops, and compare effective intervals.
- A user who wants a specific wall-clock window cannot express it yet; the
  Settings UI documents intervals only, and this ADR explains why.
- Tier changes and profile edits take effect immediately (M6), which covers the
  practical need behind most cron requests: "crawl the relevant boards more
  often, soon".
