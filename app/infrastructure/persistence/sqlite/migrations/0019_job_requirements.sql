-- M19: structured requirements and ATS keyword importance.
--
-- ``required_skills_json``/``preferred_skills_json`` stay for compatibility,
-- but they were populated from every mention.  The analyzer's full result
-- (section-aware required/preferred/mentioned skills, ATS phrases and the
-- parsed location scope) lives here, keyed by an analysis hash so stale rows
-- can be corrected in a bounded background pass.

ALTER TABLE jobs ADD COLUMN requirements_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE jobs ADD COLUMN requirements_hash TEXT NOT NULL DEFAULT '';

CREATE INDEX idx_jobs_requirements_hash ON jobs(requirements_hash);

ALTER TABLE job_keywords ADD COLUMN importance TEXT NOT NULL DEFAULT 'mentioned'
  CHECK (importance IN ('required', 'preferred', 'mentioned'));

CREATE INDEX idx_job_keywords_importance
  ON job_keywords(importance, keyword_id);
