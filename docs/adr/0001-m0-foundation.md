# ADR 0001 — Milestone 0 foundation

- Status: accepted
- Date: 2026-11-15
- Scope: Milestone 0 (architecture and desktop foundation) only

## Context

Milestone 0 must prove the architecture and ship a working desktop shell without
pre-implementing later product milestones. The specification (§4.9, §15, §16.2,
§20, §21) requires a hexagonal Python worker, a Rust/Tauri supervisor with a
generated IPC allowlist, a typed React client, and tests that prove boundaries,
swapability, and the absence of forbidden capabilities.

## Decisions

### 1. Narrow `0001_infrastructure.sql`

Only infrastructure tables are created: `schema_migrations`, `app_settings`,
`plugin_registry`, `plugin_config`, and `background_tasks`. Job, company, ATS,
resume, matching, and posting-intelligence tables are deliberately deferred to
their milestones so M0 does not ship unused schema with broken foreign keys.

Migration checksums are stored in `schema_migrations`; a database is backed up
before migrating. SQLite uses WAL, `foreign_keys=ON`, `synchronous=NORMAL`,
`busy_timeout=5000`, memory temp store, and a bounded page cache.

### 2. `diagnostics.fetch_url` is the S1 swapability subject

S1 must prove an I/O-using use case is replaceable by a fake without touching a
crawler. M0 therefore exposes a single explicit HTTP probe,
`diagnostics.fetch_url`, backed by the `HttpClient` port. It performs one
request, has no robots/retry/rate-limit policy, and is not wired into the UI. M1
introduces the real policy client and crawl pipeline; the S1 test swaps in
`FakeHttpClient` and proves no socket is opened.

### 3. A no-op long-running task proves cancellation and events

`diagnostics.sleep` runs through the real `TaskRunner`, persists lifecycle state
to `background_tasks`, and emits `task.progress` / `task.completed` /
`task.cancelled` / `task.failed`. This gives cancellation and event plumbing a
real subject in M0 without pretending to crawl.

### 4. One method catalogue, three generated artifacts

`app/worker/ipc/method_catalog.py` is the single source of truth. It feeds:

- Python dispatcher registration and tests,
- `schemas/ipc/**` JSON Schemas plus `schemas/ipc/methods.json`,
- `frontend/src/types/generated.ts`,
- Rust's compiled allowlist via `frontend/src-tauri/build.rs`.

Rust never hard-codes method names. A method absent from the catalogue cannot be
forwarded. Both Python and Rust have negative tests asserting forbidden
prefixes (`discovery.`, `jobs.`, `matching.`, `applications.`, `companies.`,
`posting_intelligence.`, `resume.`, `keywords.`, `browser.`,
`search_profiles.`) never appear.

### 5. Application depends on a registry *port*, not the plugin package

`PluginRegistry` lives in the outer `app.plugins` layer. Application services
depend on the `PluginRegistryPort` protocol in `app.domain.ports.plugins`, so
import-linter can keep `app.application` free of `app.plugins` imports while the
registry remains replaceable in tests.

### 6. Browser preview uses an explicit bridge

The frontend detects Tauri at runtime. Inside the desktop app it uses
`@tauri-apps/api` `invoke`/`listen`; in a plain browser it uses a clearly
labelled `PreviewBridge` that returns fixed data. The banner makes it impossible
to mistake preview values for worker truth. This is a development convenience,
not a second production data path.

### 7. Settings are a validated patch, not a rewritten document

`settings.update` accepts a partial `AppConfig` patch, rejects unknown keys
(strict models) and immutable runtime paths (`paths.data_dir`,
`paths.db_name`, `app_version`, `protocol_version`), deep-merges the patch over
stored overrides, validates the resulting `AppConfig`, then persists it under
`app_settings` key `app_config`. Reads always merge code defaults over stored
overrides so the UI sees a complete configuration.

### 8. Python distribution name

The distribution/package remains `lodestar` while the repository stays
`JobSignal`. Renaming the repository would break existing checkouts and links
without changing the product. The desktop product name is Job Signal throughout
the UI and Tauri configuration.

### 9. The supervisor never depends on the shell's working directory

Tauri launches `beforeDevCommand` from `frontend/` and runs the compiled shell
with `frontend/src-tauri` as its cwd, so a bare `python -m app.worker.main`
would not find the `app` package in a fresh checkout. Two consequences:

- `tauri.conf.json` uses plain `npm run dev` / `npm run build` (no `--prefix`).
- The supervisor resolves the repository root in dev builds (or from
  `LODESTAR_REPO_ROOT`), sets it as the worker's cwd, and prepends it to
  `PYTHONPATH`. A packaged build ships the worker next to the binary instead.

The restart budget (5 restarts per 5 minutes) is a `RestartPolicy` value with an
injected clock, so the sliding-window behaviour is unit-tested without spawning
processes. Live verification: killing the worker process produced a restart
2 seconds later and a new `worker.ready`.

Vite ignores `src-tauri/` and `target/` in its watcher; otherwise Windows fails
the dev server with `EBUSY` while Cargo rewrites the build directory.

## Consequences

- Later milestones append methods to one catalogue and regenerate artifacts;
  no Rust edits are needed to expose a method.
- M0 tests intentionally stop at registration/shape parity for ATS, matching,
  resume, browser, and posting-intelligence ports. They do not exercise the
  later pipelines.
- The first M1 migration must add job/source/observation tables; M0 tables are
  forward-compatible and do not reference them yet.
- The Tauri build requires frontend assets because `frontendDist` is embedded at
  compile time; CI builds the frontend before `cargo test`.
