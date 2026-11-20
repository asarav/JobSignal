-- Milestone 3: fingerprints, relations, repost events, user state, incremental hint.

ALTER TABLE jobs ADD COLUMN updated_at_hint TEXT;

CREATE TABLE job_fingerprints (
  job_id TEXT NOT NULL REFERENCES jobs(id),
  snapshot_id TEXT,
  desc_exact_hash TEXT NOT NULL,
  desc_simhash64 INTEGER,
  desc_minhash BLOB,
  title_key TEXT NOT NULL,
  location_key TEXT,
  algo_version TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (job_id, created_at)
) STRICT;

CREATE TABLE job_relations (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  related_job_id TEXT NOT NULL REFERENCES jobs(id),
  relation TEXT NOT NULL CHECK (relation IN ('similar','reposted_as','duplicate_of','user_merged','user_unmerged')),
  confidence REAL NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT 'auto'
) STRICT;

CREATE TABLE repost_events (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  previous_alias_id TEXT,
  new_alias_id TEXT,
  detected_at TEXT NOT NULL,
  class TEXT NOT NULL CHECK (class IN ('exact_duplicate','reposted_identical','materially_updated','similar_distinct','new_position')),
  confidence REAL NOT NULL,
  url_changed INTEGER NOT NULL DEFAULT 0,
  ats_id_changed INTEGER NOT NULL DEFAULT 0,
  posted_date_reset INTEGER NOT NULL DEFAULT 0,
  material INTEGER NOT NULL DEFAULT 0,
  evidence_json TEXT NOT NULL DEFAULT '[]'
) STRICT;

CREATE TABLE job_user_state (
  job_id TEXT PRIMARY KEY REFERENCES jobs(id),
  state TEXT NOT NULL CHECK (state IN ('new','saved','dismissed','hidden')),
  note TEXT,
  updated_at TEXT NOT NULL
) STRICT;
