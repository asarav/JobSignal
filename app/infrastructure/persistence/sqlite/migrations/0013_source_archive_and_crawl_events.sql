-- Milestone 7 hardening: archive sources instead of deleting them, and keep a
-- queryable record of why a crawl stopped, was refused or was blocked.

-- A source row must never be removed: jobs, job observations and crawl history
-- reference it, and with `PRAGMA foreign_keys = ON` the delete failed outright
-- (spec §15.4 wants `remove_source` to archive, §14.1 wants history preserved).
ALTER TABLE sources ADD COLUMN archived_at TEXT;

CREATE INDEX idx_sources_archived ON sources(archived_at);

-- Spec §6.6 records robots refusals, auth stops, challenge pages and schedule
-- backoff here; §17.4 promises diagnostics that can point at the entry.
CREATE TABLE crawl_events (
  id TEXT PRIMARY KEY,
  crawl_run_id TEXT NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
  at TEXT NOT NULL,
  level TEXT NOT NULL,
  code TEXT NOT NULL,
  message TEXT NOT NULL,
  url TEXT,
  plugin_id TEXT,
  data_json TEXT NOT NULL DEFAULT '{}'
) STRICT;

CREATE INDEX idx_crawl_events_run ON crawl_events(crawl_run_id, at);
