# ADR-0018: adopt a previous install's data on first launch

## Status

Accepted.

## Context

Everything the user types lives in one SQLite database under the OS app data
directory: `app_data_dir()/worker/lodestar.sqlite`, where `app_data_dir()` is
keyed by the bundle identifier (`com.jobsignal.desktop`). Installed builds
therefore upgrade in place for free — an installer replaces binaries and the
database is never in its way — and `migrator.migrate` forward-migrates the
existing file on start, snapshotting it first.

Two situations still lose the rows:

- The identifier changed once before (`com.jobsignal.app` →
  `com.jobsignal.desktop`), which silently points the app at a different
  directory.
- A reinstall can start with a fresh or cleared data directory while the old
  one is still on disk — a common outcome of uninstalling first, or of a
  per-user versus per-machine install.

The existing transfer path is the JSON backup export/import in Settings. It is
verification-first and refuses to overwrite, which is right for a restore, but
it needs the user to have exported in advance and to walk through a
confirmation. It cannot help someone who just ran the new installer.

Installer-side hooks were considered and rejected: Tauri's bundle hooks are
NSIS-only on Windows, and the same logic would have to be reimplemented for
dmg, AppImage and deb, where it would run with different permissions and no
ability to inspect SQLite. The worker already starts before any database access
and already owns migrations and integrity checks, so the transfer belongs there.

## Decision

- On start, before opening the database, the worker looks for
  `lodestar.sqlite` under known previous identifiers under the platform app data
  root, newest file first.
- Adoption only runs when the target directory is the packaged app's own data
  directory. Development (`scripts/run.py`) and test data directories, and any
  directory named by `LODESTAR_DATA_DIR`, are never adopted into.
- The candidate is **copied**, and the copy is verified before anything is
  removed: integrity, a schema this build understands, and matching row counts
  for `companies`, `jobs`, `candidate_profiles`, `resume_versions` and
  `applications` (only the tables both sides actually have, so an older schema
  still cleans up after itself).
- The previous data folder is then deleted, so exactly one live copy remains.
  Deleting is scoped to known identifiers: only `<root>/<identifier>` and
  `<root>/<identifier>/worker` can ever be adopted from or removed. If the
  removal fails (a locked file, a permission problem) the folder stays and the
  worker logs `data.adopted_previous_kept`; a duplicate is better than a loss.
- Before anything is replaced, the copy goes through `PRAGMA quick_check`, must
  contain the `jobs` table, and must report a schema version this build knows.
  A damaged file, a foreign database or one from a newer build is skipped and
  the reason is logged; the next candidate is tried.
- A successful adoption writes `adoption.json` (`source`, `adopted_at`,
  `schema_version`) into the data directory and is logged as `data.adopted`, so
  "where did these rows come from?" has an answer.
- `migrate` refuses to run when the database reports a schema version newer
  than the shipped migrations. Running an old build against a newer file is how
  data gets corrupted quietly; failing with instructions is better.
- The pre-migration snapshot now lands in `<data dir>/backups` instead of beside
  the database, which is where the integrity error already told users to look.

## Consequences

- Installing over an older build keeps working, and the identifier-rename and
  fresh-directory cases recover without any user action.
- One data directory survives an upgrade. Reinstalling the older build after
  an adoption starts it empty, because its folder is gone; that build is exactly
  what the forward-migrated database cannot serve, so this is the intended
  outcome rather than a regression.
- Downgrades fail loudly with the schema numbers and the backup directory in the
  message, instead of starting and misreading columns.
- The backup import flow stays as the explicit, cross-machine path. It is no
  longer the only way to carry data, but it remains the only way to bring an
  archive from another computer.
- Adoption cannot run while the previous app is open and writing: the copy
  would miss the newest commits. That is acceptable — installers require the
  app to be closed.
