-- Milestone 3: identity resolution wiring.
-- job_fingerprints gained the two exact-alias keys the resolver compares, and
-- the candidate/repost lookups get covering indexes.

ALTER TABLE job_fingerprints ADD COLUMN ats_key TEXT;
ALTER TABLE job_fingerprints ADD COLUMN url_key TEXT;

CREATE INDEX IF NOT EXISTS idx_job_fingerprints_job_time
  ON job_fingerprints(job_id, created_at);

CREATE INDEX IF NOT EXISTS idx_jobs_company_status
  ON jobs(company_id, posting_status);

CREATE INDEX IF NOT EXISTS idx_job_relations_related
  ON job_relations(related_job_id);

CREATE INDEX IF NOT EXISTS idx_job_relations_kind
  ON job_relations(relation);
