-- Milestone 1: discovery, sources, companies, jobs, observations, crawl runs, FTS.

CREATE TABLE sources (
  id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  kind TEXT NOT NULL,
  company_id TEXT REFERENCES companies(id),
  connector_id TEXT,
  ats_tenant_key TEXT,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
  extraction_order TEXT NOT NULL DEFAULT '[]',
  limits_json TEXT NOT NULL DEFAULT '{}',
  schedule_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE companies (
  id TEXT PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  domains_json TEXT NOT NULL DEFAULT '[]',
  ats_tenants_json TEXT NOT NULL DEFAULT '[]',
  aliases_json TEXT NOT NULL DEFAULT '[]',
  notes TEXT,
  website TEXT,
  created_at TEXT NOT NULL
) STRICT;

CREATE INDEX idx_companies_normalized ON companies(normalized_name);

CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id),
  title TEXT NOT NULL,
  normalized_title TEXT NOT NULL,
  title_family TEXT,
  description_text TEXT NOT NULL DEFAULT '',
  description_html TEXT,
  locations_json TEXT NOT NULL DEFAULT '[]',
  workplace_type TEXT NOT NULL DEFAULT 'unknown',
  employment_type TEXT NOT NULL DEFAULT 'unknown',
  seniority TEXT NOT NULL DEFAULT 'unknown',
  salary_json TEXT,
  posted_at TEXT,
  source_id TEXT NOT NULL REFERENCES sources(id),
  source_posting_id TEXT,
  canonical_url TEXT NOT NULL,
  original_urls_json TEXT NOT NULL DEFAULT '[]',
  department TEXT,
  required_skills_json TEXT NOT NULL DEFAULT '[]',
  preferred_skills_json TEXT NOT NULL DEFAULT '[]',
  extraction_status TEXT NOT NULL DEFAULT 'complete',
  extraction_confidence REAL NOT NULL DEFAULT 1.0,
  field_confidence_json TEXT NOT NULL DEFAULT '{}',
  provenance_json TEXT NOT NULL DEFAULT '[]',
  posting_status TEXT NOT NULL DEFAULT 'active',
  content_hash TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE UNIQUE INDEX idx_jobs_canonical_url ON jobs(canonical_url);
CREATE INDEX idx_jobs_company ON jobs(company_id);
CREATE INDEX idx_jobs_source ON jobs(source_id);
CREATE INDEX idx_jobs_posting_status ON jobs(posting_status);
CREATE INDEX idx_jobs_last_seen ON jobs(last_seen_at);

CREATE TABLE job_aliases (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  alias_url TEXT NOT NULL,
  alias_source_id TEXT,
  created_at TEXT NOT NULL
) STRICT;

CREATE UNIQUE INDEX idx_job_aliases_url ON job_aliases(alias_url);

CREATE TABLE job_snapshots (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  content_hash TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE job_observations (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id),
  observed_at TEXT NOT NULL,
  posting_status TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}'
) STRICT;

CREATE INDEX idx_job_observations_job ON job_observations(job_id, observed_at);

CREATE TABLE crawl_runs (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id),
  status TEXT NOT NULL CHECK (status IN ('queued','running','completed','completed_with_errors','cancelled','failed','interrupted')),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  stats_json TEXT NOT NULL DEFAULT '{}',
  error TEXT
) STRICT;

CREATE INDEX idx_crawl_runs_source ON crawl_runs(source_id, started_at);

CREATE TABLE source_health (
  source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_success_at TEXT,
  last_failure_at TEXT,
  circuit_state TEXT NOT NULL DEFAULT 'closed',
  updated_at TEXT NOT NULL
) STRICT;

-- Full-text search over jobs.
CREATE VIRTUAL TABLE jobs_fts USING fts5(
  title, description_text, company_name,
  content='jobs', content_rowid='rowid'
);

CREATE TRIGGER jobs_fts_ai AFTER INSERT ON jobs BEGIN
  INSERT INTO jobs_fts(rowid, title, description_text, company_name)
  VALUES (new.rowid, new.title, new.description_text, (SELECT canonical_name FROM companies WHERE id = new.company_id));
END;
CREATE TRIGGER jobs_fts_ad AFTER DELETE ON jobs BEGIN
  INSERT INTO jobs_fts(jobs_fts, rowid, title, description_text, company_name)
  VALUES ('delete', old.rowid, old.title, old.description_text, (SELECT canonical_name FROM companies WHERE id = old.company_id));
END;
CREATE TRIGGER jobs_fts_au AFTER UPDATE ON jobs BEGIN
  INSERT INTO jobs_fts(jobs_fts, rowid, title, description_text, company_name)
  VALUES ('delete', old.rowid, old.title, old.description_text, (SELECT canonical_name FROM companies WHERE id = old.company_id));
  INSERT INTO jobs_fts(rowid, title, description_text, company_name)
  VALUES (new.rowid, new.title, new.description_text, (SELECT canonical_name FROM companies WHERE id = new.company_id));
END;
