-- M11: audit trail for local backup exports and imports.
--
-- A backup is a portable JSON archive. Each export and each import appends one
-- row here with the archive's checksum, schema version, row counts and path, so
-- the user can see what was written or restored. Rows are append-only: an import
-- never rewrites the record of an earlier export.

CREATE TABLE backup_runs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('export', 'import')),
  path TEXT NOT NULL,
  checksum TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  app_version TEXT NOT NULL,
  table_counts_json TEXT NOT NULL DEFAULT '{}',
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
) STRICT;

CREATE INDEX idx_backup_runs_created
  ON backup_runs(created_at DESC);
CREATE INDEX idx_backup_runs_kind
  ON backup_runs(kind, created_at DESC);

CREATE TRIGGER backup_runs_no_update
BEFORE UPDATE ON backup_runs
BEGIN
  SELECT RAISE(ABORT, 'backup_runs is append-only');
END;

CREATE TRIGGER backup_runs_no_delete
BEFORE DELETE ON backup_runs
BEGIN
  SELECT RAISE(ABORT, 'backup_runs is append-only');
END;
