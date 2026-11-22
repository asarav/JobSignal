-- M5 gap closure: persisted weights, explanations, and fast result lookup.

CREATE TABLE scoring_configurations (
  id TEXT PRIMARY KEY,
  candidate_profile_id TEXT NOT NULL REFERENCES candidate_profiles(id),
  name TEXT NOT NULL,
  weights_json TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE UNIQUE INDEX idx_scoring_active
  ON scoring_configurations(candidate_profile_id)
  WHERE is_active = 1;

CREATE TABLE matching_explanations (
  id TEXT PRIMARY KEY,
  matching_result_id TEXT NOT NULL REFERENCES matching_results(id) ON DELETE CASCADE,
  reason_code TEXT NOT NULL,
  message TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '[]'
) STRICT;

CREATE INDEX idx_match_explanations_result
  ON matching_explanations(matching_result_id);

CREATE INDEX idx_match_results_profile_job
  ON matching_results(candidate_profile_id, job_id);

CREATE INDEX idx_match_results_hash
  ON matching_results(candidate_profile_id, job_id, input_hash);
