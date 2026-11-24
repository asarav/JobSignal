-- M9: per-posting keyword persistence.
--
-- Keywords are stored per posting revision, not per job. A repost with new
-- content appends a new revision, so prevalence is computed over the content
-- that was actually seen and the earlier revision stays auditable. Revisions are
-- append-only; the same (job, revision, keyword) triple is inserted once.

CREATE TABLE job_keyword_revisions (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  revision_hash TEXT NOT NULL,
  taxonomy_version TEXT NOT NULL,
  keyword_count INTEGER NOT NULL DEFAULT 0 CHECK (keyword_count >= 0),
  extracted_at TEXT NOT NULL,
  UNIQUE (job_id, revision_hash)
) STRICT;

CREATE INDEX idx_job_keyword_revisions_job
  ON job_keyword_revisions(job_id, extracted_at DESC);
CREATE INDEX idx_job_keyword_revisions_hash
  ON job_keyword_revisions(revision_hash);

CREATE TABLE job_keywords (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  revision_hash TEXT NOT NULL,
  keyword_id TEXT NOT NULL,
  mentions INTEGER NOT NULL DEFAULT 1 CHECK (mentions >= 1),
  taxonomy_version TEXT NOT NULL,
  extracted_at TEXT NOT NULL,
  UNIQUE (job_id, revision_hash, keyword_id)
) STRICT;

--: Prevalence reads scan by keyword, then group by the jobs that carry it.
CREATE INDEX idx_job_keywords_keyword
  ON job_keywords(keyword_id, revision_hash);
CREATE INDEX idx_job_keywords_job
  ON job_keywords(job_id, revision_hash);

CREATE TRIGGER job_keyword_revisions_no_update
BEFORE UPDATE ON job_keyword_revisions
BEGIN
  SELECT RAISE(ABORT, 'job_keyword_revisions is append-only');
END;

CREATE TRIGGER job_keyword_revisions_no_delete
BEFORE DELETE ON job_keyword_revisions
BEGIN
  SELECT RAISE(ABORT, 'job_keyword_revisions is append-only');
END;

CREATE TRIGGER job_keywords_no_update
BEFORE UPDATE ON job_keywords
BEGIN
  SELECT RAISE(ABORT, 'job_keywords is append-only');
END;

CREATE TRIGGER job_keywords_no_delete
BEFORE DELETE ON job_keywords
BEGIN
  SELECT RAISE(ABORT, 'job_keywords is append-only');
END;
