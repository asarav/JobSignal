# ADR-0017: Delete all local data

Status: accepted (2026-11-26)

## Context

Spec §18.2 requires a user-initiated way to remove everything Job Signal stored
locally. The SQLite database, backups, logs and exported files all live under
one data directory, and the worker process owns the database while it runs.
Deleting files underneath a live SQLite connection risks corruption and leaks
open handles, especially on Windows.

## Decision

Deletion is a supervisor command, not a worker method:

1. The Settings screen requires the user to type `DELETE` exactly. The button
   stays disabled otherwise, and the service repeats the check before invoking
   the command.
2. The Rust command sets a wipe flag, sends `system.shutdown`, and waits for the
   supervisor to observe the worker exit.
3. After exit, the supervisor removes every entry inside the configured data
   directory (files, directories, and symlinks as links) and keeps the directory
   itself. Nothing outside the data directory is touched.
4. The supervisor clears its stop flag, starts a fresh worker, and signals the
   command when the wipe finished. The UI reloads after the restart.
5. The preview build has no Tauri command, so it returns an actionable error and
   deletes nothing.

There is no second copy: no cloud sync, no recycle bin. The UI says so and
points at exporting a backup first. Automatic backups (`jobsignal-auto-*`) are
inside the same directory and are removed too.

## Consequences

- A failed wipe is reported, never treated as success. The worker does not
  restart if deletion failed.
- Success means the replacement worker reached `worker.ready`; a wipe that
  succeeds but leaves the worker unable to start returns an explicit error and
  tells the user to reopen the app. The command never reports success just
  because files were removed.
- Shutdown clears the supervisor's ready flag after the worker acknowledges
  `system.shutdown`, so a request arriving between the old worker's exit and the
  new worker's `worker.ready` cannot be sent to a stale process.
- The command has a 45-second wait; a worker that will not exit leaves the data
  intact and reports that nothing was deleted.
- Unit tests cover exact confirmation matching and path-scoped wiping. The
  packaged-app smoke test exercises a real empty-data-directory start.

## End-to-end evidence

`frontend/src-tauri/tests/delete_all_data_e2e.rs` contains
`delete_all_data_end_to_end_against_a_real_worker`. It runs the supervisor
headlessly against the frozen worker sidecar (no Tauri IPC and no mock):

1. A live data directory and database are created by the real worker.
2. Decoys are written inside the data directory and in a sibling directory
   outside it.
3. Missing and wrong confirmations are rejected; the worker PID, database,
   decoys and sibling stay untouched, and `system.hello` still succeeds.
4. `DELETE` stops the worker; the test polls the OS process table until the old
   PID is gone, then waits for a different worker PID.
5. The old decoys are gone, the data directory still exists, and every sibling
   file is byte-identical.
6. The restarted worker emits `worker.ready`, `system.hello` reports schema 18,
   and `lodestar.sqlite` exists again.

The test skips only when `binaries/lodestar-worker*` has not been built;
`desktop-build.yml` builds the sidecar and then runs this test on every target,
so a release cannot skip it. The packaged-app smoke separately proves the
bundled app starts on an empty data directory.

On the UI side, `frontend/src/features/settings/SettingsDeleteData.test.tsx`
renders the real Settings card: the button stays disabled for `delete`,
`Delete`, `DELETE!` and `yes`, enables only for `DELETE`, calls the supervisor
with that exact value, shows the restart message, clears the field so a second
click needs a fresh confirmation, and shows the refusal message without any
success text when the command is unavailable. Only the visual copy and the
real window reload remain a human release check.

Recorded local run (2026-11-26): the original worker PID exited, a different
worker PID restarted, schema 18, sibling intact.
