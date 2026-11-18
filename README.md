# Job Signal

Job Signal is a **local-first job intelligence desktop application**. Everything
runs on your machine: a Tauri 2 shell, a React UI, and a Python worker sidecar
that owns the database and all business logic. There is no account, no cloud
dependency, and no telemetry.

The repository is named `JobSignal` for historical reasons; the product is named
**Job Signal** and the Python package is **lodestar**.

> **Current milestone: 4 — Automatic discovery and daily scheduling.**
> The desktop shell, IPC contract, plugin registry, Settings and Diagnostics ship
> from Milestone 0; sources, ATS connectors and crawls from Milestone 1;
> the universal extraction pipeline from Milestone 2; search profiles, hard
> filters, full-text search and job history from Milestone 3.
> Milestone 4 makes the app find jobs on its own: discovery enumerates career
> sites from a public web index (no seed file, no user-added sources), and every
> enabled source is crawled on its own persisted schedule within per-pass
> budgets. See [`docs/how-discovery-works.md`](docs/how-discovery-works.md).

## What Milestone 4 adds

- **Seed-free discovery** (`app/plugins/discovery/web_index.py`):
  `discovery.web_index` enumerates ATS tenant boards from public web URL
  indexes — Common Crawl's index server primary, the Internet Archive CDX API
  as fallback — with no API key, no bundled list, no user input. Each pass
  walks one `(ATS host, leading-character bucket)` slice of the tenant space,
  so coverage grows every day and wraps around to refresh. A slice whose
  indexes are all unreachable is retried next pass (the rotation cursor is
  held). Design and trade-offs:
  [ADR 0005](docs/adr/0005-m4-automatic-discovery.md).
- **The seed file is a bootstrap, not the engine.**
  `discovery.ats_directory` ships eight public boards as package data so a
  cold install has something to crawl immediately and tests do not need the
  network. Deleting it does not stop discovery.
- **`discovery.auto_discover`** (`app/application/auto_discover.py`): asks
  every enabled provider for candidates, dedupes by canonical URL, registers
  up to `discovery.max_new_sources_per_run` new sources (connector from the
  provider's ATS hint or the URL classifier), then crawls the sources that are
  due — oldest attempt first, up to `discovery.max_crawl_sources_per_run`.
  Per-provider and per-source failures are isolated and counted, never fatal.
- **Bounded work.** A source is crawled by the scheduler and by the pass only
  through one shared crawl gate (`crawl.global_concurrency`); the pass has a
  registration cap and a crawl cap, so a large source list never turns a pass
  into an hours-long run. Nothing crawls on app launch.
- **Cadence and coverage survive restarts.** The last run time *and* the
  rotation cursor are stored in `app_settings` (`auto_discover_state`). The
  loop waits `AUTO_DISCOVER_STARTUP_DELAY_S` after launch, then checks every
  five minutes whether a run is due (`discovery.auto_discover_interval_hours`,
  default 24).
- **Crawl events.** Every triggered crawl records a `crawl_runs` row and
  publishes `crawl.completed` / `crawl.failed`, so new jobs flow straight into
  search profiles (hard filters apply to searches as in M3).
- **Discover screen**: an "Auto-discovery" panel that says the pass runs by
  itself, mentions the coverage limit, and offers a manual run that reports
  sites found, new sources, crawled and failed counts.

## What Milestone 2 includes

- A **universal extraction pipeline**: strategies run in order (per-source
  override supported) and merge their results field by field, keeping the most
  confident value. Every field carries its strategy as provenance.
- Six **extraction strategies**: `public_endpoint`, `jsonld`, `embedded_json`,
  `static_html`, `generic`, `partial`, plus `rendered_dom` when a browser is
  available.
- **Rendered-DOM extraction** via an optional Playwright dependency
  (`pip install "lodestar[render]"` then `playwright install chromium`). A page
  that disallows crawling, or that is a challenge/CAPTCHA page, is not
  extracted at all.
- **Three more ATS connectors**: SmartRecruiters, Workday and iCIMS, all
  behind the `JobSourceConnector` port and contract-tested.
- **HTML parsing uses the standard library** (`html.parser`), so no new
  required dependency.
- **Provenance** surfaces in the data model and the Discover screen.


## Still not implemented

These are **intentionally absent** in Milestone 4, and the IPC allowlist
rejects them (there is a test for it):

- Resume parsing, candidate profiles, keyword extraction — Milestone 5
- Job matching, scoring, ranking, and saved jobs — Milestone 5
- Application tracking, suppression, company intelligence, outcome analytics — Milestone 6
- Ghost-job / posting-quality risk models — Milestone 7
- Outcome feedback and analytics — Milestone 8
- Posting intelligence UI refinement — Milestone 9
- Hardening and distribution — Milestone 10

No browser automation is installed by default; Playwright is an opt-in extra.

## Repository layout

```text
app/
  domain/              # models, enums, policies, ports (Protocols), services
  application/         # commands, DTOs, orchestration, configuration, container
  infrastructure/      # SQLite, in-memory twins, HTTP, logging, clock, ids
  plugins/             # registry, isolation shim, builtin + ATS connectors
  worker/              # composition root, IPC dispatcher/transport, entrypoint
frontend/
  src/                 # React 19 + TypeScript routes, stores, services, copy
  src-tauri/           # Tauri 2 shell + Python supervisor + generated allowlist
schemas/ipc/           # generated JSON Schemas; methods.json is the allowlist
scripts/               # gen_schemas.py, gen-ts-types.mjs, dev.ps1
tests/
  unit/ contract/ integration/ safety/ swapability/ support/
docs/adr/              # architecture decision records
.github/workflows/     # CI matrix
```

Where the discovery code lives:

```text
app/domain/services/     # classifier.py, normalizer.py  (pure domain logic)
app/application/         # sources.py (registry + crawl runner), auto_discover.py,
                         # orchestration/scheduler.py, commands/
app/plugins/ats/         # greenhouse/, ashby/, lever/, smartrecruiters, workday, icims
app/plugins/discovery/   # web_index.py (seed-free enumeration), ats_directory.py (bootstrap)
app/infrastructure/      # persistence (sources, jobs, snapshots, crawl runs)
```

Documentation for M4 lives in
[`docs/how-discovery-works.md`](docs/how-discovery-works.md) (user-facing: what
runs, what leaves the machine, coverage limits) and
[`docs/adr/0005-m4-automatic-discovery.md`](docs/adr/0005-m4-automatic-discovery.md)
(why discovery is seed-free, budgets, and the options that were rejected).

### Dependency rule

```text
domain (stdlib only)
   ▲
application (domain only)
   ▲
infrastructure / plugins (domain; bundles application DTOs at the ends)
   ▲
worker (composition root; may import everything)
```

`import-linter` enforces it in CI:

- domain must not import application, infrastructure, plugins, or worker
- application must not import infrastructure, plugins, or worker
- infrastructure must not import worker
- plugins must not import worker or application

Rust contains **no business logic** and never opens SQLite. It only spawns,
supervises, sizes, and forwards envelopes.

## Prerequisites

- Python **3.12+** (3.14 is used locally)
- Node.js **20+** (22 LTS in CI) and npm
- Rust stable (`rustup`) — after installing, add `%USERPROFILE%\.cargo\bin` to
  your `PATH` (restart the terminal) so `cargo` is discoverable by npm/Tauri.
- Tauri 2 system dependencies (Linux: `libwebkit2gtk-4.1-dev`,
  `libappindicator3-dev`, `librsvg2-dev`, `patchelf`; macOS: Xcode command line
  tools; Windows: WebView2, preinstalled on current Windows)
- No external SQLite install is required (`aiosqlite` bundles nothing extra and
  uses the platform SQLite).

## Setup and common commands

```bash
# Python
python -m pip install -e ".[dev]"

# Frontend
cd frontend
npm ci
```

### Run the worker directly

The worker requires an explicit data directory and speaks NDJSON on
stdin/stdout. stderr is structured JSON logs.

```bash
python -m app.worker.main --data-dir ./.lodestar-data
```

The database is `<data-dir>/lodestar.sqlite`; rotating logs live in
`<data-dir>/logs`.

### Run the desktop app

```powershell
# Windows: make sure the Rust toolchain is on PATH first
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
cd frontend
npm run tauri dev
```

Or use the helper script:

```powershell
.\scripts\dev.ps1
```

The Tauri supervisor spawns `python -m app.worker.main --data-dir
<app_data_dir>/worker`. In a dev build it also adds the repository root to
`PYTHONPATH` and runs the worker from there, so no `pip install` is required to
try the desktop app. Override with environment variables when needed:

- `LODESTAR_PYTHON` — Python executable (default: `python`)
- `LODESTAR_DATA_DIR` — explicit worker data directory
- `LODESTAR_REPO_ROOT` — Python source tree to run from (defaults to the repo
  root baked into the dev build)

A crashed worker is restarted with exponential backoff, capped at **5 restarts
in 5 minutes**.

### Dev database and schema checksums

The migrator enforces a per-migration checksum (SHA256 of the `.sql` file)
stored in `schema_migrations`. If a migration file changes between runs — for
example after pulling a new milestone — the stored checksum no longer matches
and the worker refuses to start, crash-looping until the restart budget is
exhausted.

The dev script `.\scripts\dev.ps1` handles this automatically: it removes the
ephemeral dev database at `%APPDATA%\com.jobsignal.app\worker\lodestar.sqlite`
before launching Tauri, so the worker applies the current schema and emits
`worker.ready`. This only touches local dev data; never production data.

To reset the database manually:

```powershell
Remove-Item "$env:APPDATA\com.jobsignal.app\worker\lodestar.sqlite*" -ErrorAction SilentlyContinue
```


### Run the UI in a browser (no Tauri)

```bash
cd frontend
npm run dev
```

A clearly labelled **Preview mode** banner appears and an explicit
`PreviewBridge` returns placeholder data. The desktop app always uses the real
Tauri bridge.

## Tests, lint, and generation

```bash
# Python: unit + contract + integration + safety + swapability (S1-S9)
python -m pytest -q

# Python lint and architecture
python -m ruff check app tests
python -m mypy
python -c "from importlinter.cli import lint_imports_command; lint_imports_command()"

# Frontend
cd frontend
npm run typecheck
npm test
npm run build

# Rust supervisor (allowlist negative test + backoff)
cd frontend/src-tauri
cargo test --lib

# Regenerate schemas and TypeScript types
python scripts/gen_schemas.py
node scripts/gen-ts-types.mjs
```

`python scripts/gen_schemas.py --check` and
`node scripts/gen-ts-types.mjs --check` fail when committed generated files are
stale. CI runs both and then `git diff --exit-code`.

Integration tests spawn the real worker subprocess and cover the handshake,
hello/health, invalid params, forbidden methods, settings validation, plugin
listing, task progress/completion/persistence, cancellation, and graceful
shutdown. The worker uses the repository's `sys.executable`, so no extra setup
is needed.

`tests/safety/` guards the milestone scope: no crawling, browser, resume, or
machine-learning dependency may appear in `pyproject.toml` or in production
imports, migration `0001` may only create the M0 infrastructure tables, and no
forbidden IPC method may be registered.

## IPC protocol v1 (summary)

Transport: **NDJSON** over the worker's stdin/stdout, one JSON object per line,
maximum **8 MB** per line. stderr is for structured logs only.

Every envelope has `v: 1` and a `type` of `request`, `response`, or `event`.

On startup the worker emits:

```json
{
  "v": 1,
  "type": "event",
  "topic": "worker.ready",
  "seq": 0,
  "ts": "2026-01-01T00:00:00Z",
  "data": {
    "protocol_versions": [1],
    "app_version": "0.0.0",
    "schema_version": 1
  }
}
```

The UI then performs `system.hello` to negotiate protocol v1. Requests carry an
`id`; responses echo it with `ok` plus `result` or `error {code, message,
retryable, details}`.

### M0 methods

| Method | Purpose |
|---|---|
| `system.hello` | negotiate protocol v1 and return worker capabilities |
| `system.health` | status, DB schema version, data dir/disk usage, plugin health, queue depth |
| `system.shutdown` | graceful worker stop |
| `tasks.list` | list persisted `background_tasks` |
| `tasks.cancel` | cooperative cancel (no-op success for unknown tasks) |
| `settings.get` | merged `AppConfig` plus stored overrides |
| `settings.update` | validate and persist a settings patch |
| `plugins.list` | registry entries, state, capabilities, health |
| `plugins.enable` / `plugins.disable` | toggle and persist plugin state |
| `plugins.configure` | validate/persist plugin config |
| `plugins.get_capabilities` | capability map (optionally for one plugin) |
| `plugins.reset_circuit` | reset a plugin's circuit breaker |
| `diagnostics.fetch_url` | one sandboxed HTTP request (not a crawler) |
| `diagnostics.sleep` | no-op long-running task used to test progress/cancel/events |
| `sources.list` | configured job sources with their connector and interval |
| `sources.add` | classify a URL, store it as a source |
| `sources.set_enabled` | pause/resume a source |
| `sources.set_schedule` | change the crawl interval (0 disables scheduling) |
| `sources.delete` | remove a source |
| `jobs.list` | persisted jobs, optionally filtered by source or company |
| `jobs.search` | full-text search over jobs (FTS5) |
| `crawl_runs.list` | crawl history with per-run stats |
| `discovery.run` | crawl one source now, returning its stats |

The authoritative catalogue is
`app/worker/ipc/method_catalog.py`. It generates:

- `schemas/ipc/**/*.json` (via `scripts/gen_schemas.py`)
- `schemas/ipc/methods.json` (the method allowlist)
- `frontend/src/types/generated.ts` (via `scripts/gen-ts-types.mjs`)
- the Rust allowlist (via `frontend/src-tauri/build.rs`)

Rust exposes two commands, `worker_request({ method, params })` and
`worker_shutdown`, and forwards worker events on `worker://event`. Rust never
interprets payloads; it enforces the generated allowlist and envelope size.

Long-running work emits `task.queued`, `task.progress`, `task.completed`,
`task.cancelled`, and `task.failed` events with an optional `task_id`.
Crawls emit `crawl.started`, `crawl.completed`, and `crawl.failed`.

## Settings model

Defaults come from `AppConfig` (spec §20). User changes are validated as a
strict patch and stored under the `app_config` key in `app_settings`; reads merge
the patch over code defaults.

`paths.data_dir`, `paths.db_name`, `app_version`, and `protocol_version` are
**immutable for a run** and are rejected with `permission.denied`. Unknown keys
are rejected with `validation.invalid_params`.

## Adding a builtin plugin

Adding a plugin does not require touching application or UI code:

1. Create `app/plugins/<kind>/<name>/__init__.py` exposing a `PLUGIN` manifest
   and a `register(registry, deps)` function, or a module-level `register`.
2. Register a `PluginManifest` with a unique `id`, `kind`, `version`,
   `api_version="1"`, capabilities, and optional `network_hosts`.
3. Add one `(module_path, "register")` entry to `BUILTIN_PLUGINS` in
   `app/plugins/_builtin.py`.

Plugins are wrapped by `IsolationShim`: calls get a timeout, exceptions become
`PortError`s, and a circuit breaker opens after 5 consecutive failures or when
≥50% of the last 20 calls fail. Health is persisted to `plugin_registry`.

M0 ships exactly one builtin registration smoke plugin, `extract.demo`, which
performs no extraction. Test-only toy plugins (ATS connector, signal provider,
constant scoring model, extraction strategy) live in `tests/support/`.

## Architecture decisions

- [`docs/adr/0001-m0-foundation.md`](docs/adr/0001-m0-foundation.md) — M0 scope
  decisions, the deliberately narrow `0001` migration, the diagnostics
  `FetchUrl` swapability subject, and the browser preview bridge.
- [`docs/adr/0002-m1-discovery.md`](docs/adr/0002-m1-discovery.md) — the M1
  discovery framework: source registry, normalizer v1, incremental crawls,
  append-only snapshots/aliases, stable company identity, and the scheduler.
- [`docs/adr/0004-m2-extraction.md`](docs/adr/0004-m2-extraction.md) — the M2
  extraction pipeline, field-level merge, rendered-DOM via an optional
  Playwright dependency, and the isolation shim for connectors.

## CI

`.github/workflows/ci.yml` runs a matrix across **Windows, macOS, and Ubuntu**:

- Python: `ruff`, strict `mypy`, `import-linter`, full `pytest`
- Frontend: `tsc --noEmit`, `vitest`, production `vite build`
- Rust: `cargo test --lib` with the generated allowlist and backoff tests
- Schema job: regenerates JSON Schemas and TypeScript types and fails on drift

Reproduce any CI job locally with the commands in
[Tests, lint, and generation](#tests-lint-and-generation).

## Security and privacy notes

- Local-only SQLite; no network calls on startup.
- `diagnostics.fetch_url` is a single explicit HTTP probe, not a crawler and not
  called by any UI flow today.
- Logs redact resume text, answer books, email addresses, phone numbers, and
  password fields (`app/infrastructure/logging`).
- The program never submits job applications. Browser automation is out of scope
  for M0 and no Playwright dependency is installed.
