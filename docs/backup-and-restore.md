# Backup and restore

Job Signal keeps all of your data in one local SQLite database. A backup is a
JSON file you own; a restore fills a fresh install from it.

## Export

Settings → **Backup and restore** → **Export a backup** (`system.export_backup`).
The file is written inside `<data dir>/backups` with a timestamped name, and the
response shows its path, checksum, schema version and per-table row counts. Every
export is appended to the backup history on the same screen
(`system.get_backup_history`).

You can pass a relative path (`backups/before-upgrade.json`) but never one that
resolves outside the data directory.

## Restore

A restore **only fills an empty database**. There is no overwrite mode, because
the history tables in this project are append-only and the point of a restore is
to rebuild a fresh install.

1. Copy the backup file into `<data dir>/backups` on the new machine.
2. Enter its relative path in Settings → Backup and restore.
3. Tick the confirmation — *"I understand a restore only fills an empty database
   and cannot overwrite existing data."*
4. Press **Restore from a backup** (`system.import_backup`).

What happens:

- Without `confirm: true` the call fails with
  `validation.confirmation_required`.
- If any restorable table already has a row, the call fails with
  `conflict.backup_not_empty` and nothing is changed.
- The archive's checksum and format are validated first; a corrupted or edited
  file fails with `validation.invalid_backup`.
- Rows are inserted in one transaction, then every table's row count is compared
  with the archive's counts. Only then does the response say `verified: true`;
  a mismatch rolls back with `internal.backup_verification_failed`.

The import appends its own row to the backup history, so both directions are
auditable.

## What is in a backup

Every user table — profile, postings, companies, applications and their
append-only history, keyword revisions, fit weight versions, risk assessments,
observations and endpoint checks — plus the schema version, app version and
checksums.

Not included: `schema_migrations` (the installed build owns its migrations),
`backup_runs` (local audit of this machine) and the FTS shadow tables (rebuilt
from the copied text).

## Automatic backups

The maintenance loop checks `backup.enabled` (default on). The first check
records a baseline in `app_settings` and writes nothing; after
`backup.interval_days` (default 7) it writes
`backups/jobsignal-auto-<timestamp>.json`. Retention keeps the newest
`backup.keep` (default 4) automatic archives and never deletes a manual export.
Both settings live in `AppConfig` and are validated like every other section.
The history table records automatic exports alongside manual ones.

## Upgrading and reinstalling

You do not need a backup to keep your data across versions. The database lives
in the OS app data directory and the installer only replaces binaries, so
installing a newer build over an older one keeps every row; the new build
forward-migrates the existing file on start, after writing a snapshot to
`<data dir>/backups`.

If the app data location itself changes — an older build that used a different
bundle identifier, or a reinstall that starts in a fresh directory while the old
one is still on disk — the worker copies the previous database into place before
opening it. The copy is checked with `PRAGMA quick_check`, must be a Job Signal
database, and must not come from a newer build. Its row counts for the core
tables are then compared with the source, and only when they match is the
previous data folder deleted, so one live copy remains.
`<data dir>/adoption.json` records where the rows came from and whether the old
folder was removed. A refused database is never deleted, and a folder that
cannot be removed (a locked file, a permission problem) is kept with a logged
warning instead of failing startup.

A directory named by `LODESTAR_DATA_DIR`, and the development launcher's
`.lodestar-data`, are never adopted into: an explicit data directory always wins.

Downgrades are refused. If the database reports a schema newer than the build
ships, the worker stops with the two schema numbers and the path to
`<data dir>/backups` instead of reading columns it does not understand.

## Damaged databases

Startup runs `PRAGMA quick_check` after applying migrations. If SQLite reports
damage, the worker stops before serving requests and names this document's
restore steps. Restore the newest usable file from `<data dir>/backups` — either a
pre-migration `.bak` snapshot or an exported `.json` archive — into an empty
data directory; the worker will not write into a damaged database.

## Notes

- A backup is **not** encrypted by this feature. It contains your answer book and
  application history; store it somewhere you would store that data.
- Restores are verification-first: a file that does not round-trip leaves the
  database exactly as it was.
- See [ADR-0015](adr/0015-local-backup-and-restore.md) for the reasoning.
