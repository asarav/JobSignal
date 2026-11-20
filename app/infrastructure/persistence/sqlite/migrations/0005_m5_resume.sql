-- Milestone 5: resume parsing, candidate profiles, matching.

CREATE TABLE candidate_profiles (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  active_resume_version_id TEXT,
  contact_json TEXT NOT NULL DEFAULT '{}',
  answer_book_json TEXT NOT NULL DEFAULT '{}',
  overrides_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE resume_files (
  id TEXT PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE,
  original_name TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  stored_path TEXT NOT NULL,
  imported_at TEXT NOT NULL
) STRICT;

CREATE TABLE resume_versions (
  id TEXT PRIMARY KEY,
  candidate_profile_id TEXT NOT NULL REFERENCES candidate_profiles(id),
  resume_file_id TEXT NOT NULL REFERENCES resume_files(id),
  label TEXT NOT NULL,
  parent_version_id TEXT REFERENCES resume_versions(id),
  parser_id TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  parsed_json TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE extracted_skills (
  id TEXT PRIMARY KEY,
  resume_version_id TEXT NOT NULL REFERENCES resume_versions(id),
  keyword_id TEXT NOT NULL,
  evidence TEXT NOT NULL,
  confidence REAL NOT NULL,
  duration_years REAL,
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  UNIQUE (resume_version_id, keyword_id)
) STRICT;

CREATE TABLE experience_records (
  id TEXT PRIMARY KEY,
  resume_version_id TEXT NOT NULL REFERENCES resume_versions(id),
  employer TEXT,
  title TEXT,
  start_date TEXT,
  end_date TEXT,
  is_current INTEGER NOT NULL DEFAULT 0,
  bullets_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL
) STRICT;

CREATE TABLE education_records (
  id TEXT PRIMARY KEY,
  resume_version_id TEXT NOT NULL REFERENCES resume_versions(id),
  institution TEXT,
  degree TEXT,
  field TEXT,
  start_date TEXT,
  end_date TEXT,
  confidence REAL NOT NULL
) STRICT;

CREATE TABLE profile_items (
  id TEXT PRIMARY KEY,
  resume_version_id TEXT NOT NULL REFERENCES resume_versions(id),
  kind TEXT NOT NULL CHECK (kind IN ('certification','project')),
  name TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}'
) STRICT;

CREATE TABLE user_skill_status (
  candidate_profile_id TEXT NOT NULL REFERENCES candidate_profiles(id),
  keyword_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('have_experience','learning','no_experience','not_relevant')),
  updated_at TEXT NOT NULL,
  PRIMARY KEY (candidate_profile_id, keyword_id)
) STRICT;

CREATE TABLE matching_results (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  candidate_profile_id TEXT NOT NULL REFERENCES candidate_profiles(id),
  resume_version_id TEXT REFERENCES resume_versions(id),
  model_id TEXT NOT NULL,
  model_version TEXT NOT NULL,
  weights_json TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  fit_score REAL NOT NULL,
  dimension_coverage REAL NOT NULL,
  evaluated_at TEXT NOT NULL
) STRICT;

CREATE TABLE matching_dimension_scores (
  id TEXT PRIMARY KEY,
  matching_result_id TEXT NOT NULL REFERENCES matching_results(id) ON DELETE CASCADE,
  dimension TEXT NOT NULL,
  strategy_id TEXT NOT NULL,
  strategy_version TEXT NOT NULL,
  score REAL,
  available INTEGER NOT NULL,
  weight REAL NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}'
) STRICT;

CREATE TABLE keyword_taxonomy (
  id TEXT PRIMARY KEY,
  canonical TEXT NOT NULL,
  category TEXT NOT NULL,
  aliases_json TEXT NOT NULL DEFAULT '[]',
  implies_json TEXT NOT NULL DEFAULT '[]',
  version TEXT NOT NULL
) STRICT;
