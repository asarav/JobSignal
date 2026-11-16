CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL,
  checksum TEXT NOT NULL
) STRICT;

CREATE TABLE app_settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  schema_version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE plugin_registry (
  plugin_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  version TEXT NOT NULL,
  api_version TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
  health_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE plugin_config (
  plugin_id TEXT NOT NULL REFERENCES plugin_registry(plugin_id) ON DELETE CASCADE,
  config_json TEXT NOT NULL,
  config_schema_version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (plugin_id)
) STRICT;

CREATE TABLE background_tasks (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  status TEXT NOT NULL
    CHECK (status IN ('queued','running','completed','completed_with_errors','cancelled','failed','interrupted')),
  priority INTEGER NOT NULL DEFAULT 5,
  params_json TEXT NOT NULL DEFAULT '{}',
  progress_json TEXT NOT NULL DEFAULT '{}',
  error_json TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  parent_task_id TEXT REFERENCES background_tasks(id)
) STRICT;

CREATE INDEX idx_tasks_status ON background_tasks(status, priority, created_at);
