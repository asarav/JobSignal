# ADR 0002 — Milestone 1 discovery framework

- Status: accepted
- Date: 2026-11-16
- Scope: Milestone 1 (discovery framework and initial connectors)

## Context

Milestone 0 shipped the desktop shell, the IPC contract, the plugin registry
and the SQLite foundation, but no way to turn a careers URL into stored jobs.
Milestone 1 has to deliver that pipeline (spec §21, Milestone 1): source
discovery and classification, a policy-aware HTTP client, initial ATS
connectors, a crawl runner, normalized job persistence, full-text search, and
the first real product screen.

The constraints that shaped the design:

- The dependency direction must not change: `app.application` may not import
  `app.infrastructure`.
- Crawls must be **incremental**: re-running a crawl over an unchanged board
  must not rewrite history.
- History must be **append-only** (spec §23.8): observations and snapshots are
  records, not mutable rows.
- A failing source must never break the other sources.
- Every milestone ships with tests, docs and a short report.

## Decisions

### 1. One method catalogue, extended rather than duplicated

`app/worker/ipc/method_catalog.py` remains the single source of truth. M1
appends `sources.*`, `jobs.*`, `crawl_runs.list` and `discovery.run`; the
generated JSON Schemas, the TypeScript types and the Rust allowlist all follow
automatically. The Rust allowlist is compiled from
`schemas/ipc/methods.json`, so exposing a method never requires a Rust edit.

The catalogue was renamed from `M0_METHODS` to `MILESTONE_METHODS` because the
name would otherwise lie as soon as M1 landed.

### 2. The normalizer lives in the domain, not in the crawl runner

`app/domain/services/normalizer.py` implements strategy `normalize.v1`: it
turns a raw posting into `NormalizedJob`, and it only asserts a field when the
payload or the title supports it. Everything else stays `unknown` rather than
being guessed, and every asserted field carries a confidence value.

An earlier draft put a stub normalizer inside the application layer. That was
rejected: normalization is domain logic, it must be testable without I/O, and
it must be reusable by later milestones (M2 extraction strategies).

### 3. Stable company identity

Company ids are derived from the normalized company name
(`company_id_for`), not generated randomly. A random id created one company row
per crawl, which duplicated companies in the UI and made company-level
queries meaningless. The company name itself comes from the ATS tenant key when
available, otherwise from the source URL.

### 4. Incremental crawls are decided by content hash

A posting is fingerprinted by `sha256(title + description)`. When the stored
hash matches, the crawl records an observation and skips the job — no write to
`jobs`, no new snapshot. When it differs, the job is updated and a new
snapshot is appended. The crawl stats report `new`, `updated`, `skipped`,
`removed` and `errors` so the behaviour is visible in the UI and in tests.

### 5. Snapshots and aliases are append-only

`job_snapshots` stores one row per observed content change, and `job_aliases`
stores alternative URLs that resolve to the same job. Both were created by
migration `0002` but were not written by the first implementation — dead
schema. Milestone 1 writes them: a posting that moves to a new URL keeps its
job and gains an alias row instead of becoming a duplicate.

### 6. The crawler depends on the `HttpClient` port

The application layer receives the `HttpClient` protocol, not
`PolicyHttpClient`. The policy client is an adapter chosen in the worker
composition root. This keeps the import-linter contract "application may not
import infrastructure" true, and it lets tests inject a fake transport.

### 7. Failures are isolated per source

`run_crawl` catches any exception from the connector, records the crawl run as
`failed` with the reason, publishes a `crawl.failed` event, and returns the
stats collected so far. Other sources keep working.

### 8. A simple in-process scheduler

`CrawlScheduler` runs one task per source on the interval stored in the
source's `schedule.interval_s`. It is deliberately not a daemon: cron
expressions, jitter and persisted schedules land in M8. The worker reschedules
whenever a `sources.*` method changes a source, and cancels all tasks on
shutdown.

### 9. The dev database resets only when the schema changes

The migrator checksums every migration file. `scripts/dev.ps1` compares a hash
of the migration files with a hash stored next to the dev database and removes
the database only when they differ. An earlier version removed the database on
every launch, which silently discarded local dev data.

## Consequences

- Adding an ATS means writing one connector plus a contract test; no
  application, IPC or UI code changes.
- `jobs` rows are the current state, `job_snapshots` and `job_observations` are
  the history. Later milestones (M3 repost detection, M6 risk assessment) build
  on that history rather than re-crawling.
- The scheduler is in-process: it dies with the worker. Persisted schedules and
  catch-up runs are M8 work.
- Content-hash comparison detects *changed* postings, not *removed* ones; the
  removal logic marks postings that the board no longer lists. M3 will refine
  this with fingerprints and repost classes.
