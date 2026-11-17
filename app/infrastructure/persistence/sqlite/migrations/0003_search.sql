-- Milestone 3: search profiles.

CREATE TABLE search_profiles (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  candidate_profile_id TEXT,
  scoring_config_id TEXT,
  ranking_policy TEXT NOT NULL DEFAULT 'deprioritize',
  hard_filters_json TEXT NOT NULL DEFAULT '{}',
  preferences_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE INDEX idx_search_profiles_name ON search_profiles(name);
