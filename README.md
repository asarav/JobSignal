# Job Signal

Job Signal is a **local-first job intelligence desktop application**. Everything
runs on your machine: a Tauri 2 shell, a React UI, and a Python worker sidecar
that owns the database and all business logic. There is no account, no cloud
dependency, and no telemetry.

The repository is named `JobSignal` for historical reasons; the product is named
**Job Signal** and the Python package is **lodestar**.

> **Current milestone: 0 — Architecture and desktop foundation.**
> This build ships the desktop shell, IPC contract, SQLite infrastructure,
> plugin registry, Settings, and Diagnostics. It is not a job search product yet.

## What Milestone 0 includes

- Tauri 2 desktop shell with a supervised Python worker (`python -m app.worker.main`).
- NDJSON IPC protocol v1 with generated JSON Schemas and typed TypeScript clients.
- Hexagonal Python core: domain models and `typing.Protocol` ports, immutable
  composition-root `Container`, application use cases, infrastructure adapters.
- SQLite migration `0001_infrastructure.sql` with `schema_migrations`,
  `app_settings`, `plugin_registry`, `plugin_config`, and `background_tasks`.
- Plugin registry with duplicate-ID/API-version checks, per-plugin timeouts,
  exception capture, and a circuit breaker with persisted health.
- A real Settings screen (data dir, feature flags, plugin registry) and a real
  Diagnostics screen (health, recent tasks, progress/cancel, event feed).
- Unit, contract, integration, and swapability (S1–S9) tests.
- CI on Windows, macOS, and Ubuntu for Python, frontend, Rust, and schema drift.

## Not yet implemented

These are **intentionally absent** in Milestone 0, and the IPC allowlist rejects
them (there is a test for it):

- Job crawling, discovery providers, search-engine discovery, crawl scheduling
- ATS connectors and job-source integrations
- Resume parsing, candidate profiles, keyword extraction
- Job matching, scoring, ranking, and saved jobs
- Ghost-job / posting-quality risk models
- Browser automation, form inspection, and autofill/Playwright
- Application tracking, suppression, company intelligence, outcome analytics

No such production code is present. Only test-only toy plugins and one builtin
extraction *registration smoke* plugin (`extract.demo`) exist.

## Repository layout

```text
app/
  domain/              # models, enums, policies, ports (Protocols)
  application/         # commands, DTOs, orchestration, configuration, container
  infrastructure/      # SQLite, in-memory twins, HTTP, logging, clock, ids
  plugins/             # registry, isolation shim, builtin registration hook
  worker/              # composition root, IPC dispatcher/transport, entrypoint
frontend/
  src/                 # React 19 + TypeScript routes, stores, services, copy
  src-tauri/           # Tauri 2 shell + Python supervisor + generated allowlist
schemas/ipc/           # generated JSON Schemas; methods.json is the allowlist
scripts/               # gen_schemas.py, gen-ts-types.mjs
tests/
  unit/ contract/ integration/ safety/ swapability/ support/
docs/adr/              # architecture decision records
.github/workflows/     # CI matrix
```

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

See [`docs/adr/0001-m0-foundation.md`](docs/adr/0001-m0-foundation.md) for the
M0 scope decisions, the deliberately narrow `0001` migration, the diagnostics
`FetchUrl` swapability subject, and the browser preview bridge.

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
