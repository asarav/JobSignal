# ADR-0015 — Local backup and restore

- **Status:** Accepted (M11)
- **Date:** 2026-11-24
- **Supersedes:** nothing

## Context

Everything Job Signal knows lives in one local SQLite file under the data
directory. A user who moves to a new machine, or who wants an extra copy before
a risky operation, has no supported way to take their data with them. Copying the
database by hand is possible but easy to get wrong while the app is running, and
it carries no check that the copy is complete.

The plan's M11 section asks for a backup/import surface with an explicit
confirmation, no silent overwrite, and a verified round trip. The history tables
in this project are append-only by trigger, so a restore cannot delete rows to
make room for different ones.

## Decision

1. **One portable archive format, `jobsignal.backup/1`.** A backup is a
   UTF-8 JSON file with the schema version, the app version, the ordered list of
   tables, each table's columns and rows, per-table row counts, and a
   `sha256` checksum over the canonical serialization of the payload
   (`app/domain/services/backup_archive.py`). Files are written under
   `<data_dir>/backups`, and both export and import refuse any path that
   resolves outside that directory.
2. **Import only fills an empty database.** `system.import_backup` requires
   `confirm: true`; without it the call fails with
   `validation.confirmation_required`. If any restorable table already holds a
   row, the import is refused with `conflict.backup_not_empty` and every
   existing row is left untouched. There is deliberately **no overwrite mode**:
   append-only history means a true overwrite would either fail or require
   disabling the triggers that protect the audit trail.
3. **The round trip is verified, not assumed.** After inserting, the importer
   re-reads every table's row count and compares it with the archive's counts. A
   mismatch rolls the transaction back and reports
   `internal.backup_verification_failed`; the response only says
   `verified: true` when the counts match.
4. **Imports run inside one transaction with foreign keys deferred.** Tables are
   inserted in dependency order with `PRAGMA foreign_keys = OFF` for the import
   transaction only, then foreign keys are checked again before commit, so an
   archive cannot leave a half-linked database.
5. **Both directions are audited.** Migration `0018_backup_runs` adds the
   append-only `backup_runs` table (`BEFORE UPDATE`/`BEFORE DELETE` abort
   triggers). Each export and import appends one row with its kind, path,
   checksum, schema version and timestamp, and `system.get_backup_history`
   exposes it in the Settings screen.
6. **Excluded tables.** `schema_migrations`, `backup_runs` and the FTS shadow
   tables are not copied: migrations belong to the installed build, the audit
   is local to this machine, and FTS content is rebuilt from the copied text.

## Consequences

- A backup restores into a fresh install, which is the case the feature exists
  for. Merging two non-empty databases stays out of scope; the conflict code
  says so instead of guessing.
- The archive is plain JSON, so a user can inspect it, but it also means a
  backup of a very large database is correspondingly large. `MAX_ARCHIVE_BYTES`
  bounds what the importer will read.
- The append-only guarantee now also covers the backup audit: neither direction
  can be rewritten after the fact.
- Changing a restorable table's columns requires bumping the archive format
  version; the validator rejects unknown table names and missing columns.
