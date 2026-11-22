-- Milestone 7: application history, company-aware suppression, user job state.

CREATE TABLE applications (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  job_title TEXT NOT NULL,
  job_title_family TEXT,
  job_canonical_url TEXT NOT NULL DEFAULT '',
  job_description_text TEXT NOT NULL DEFAULT '',
  job_department TEXT,
  job_locations_json TEXT NOT NULL DEFAULT '[]',
  job_workplace_type TEXT NOT NULL DEFAULT 'unknown',
  job_snapshot_id TEXT REFERENCES job_snapshots(id),
  applied_at TEXT,
  status TEXT NOT NULL CHECK (status IN (
    'interested','saved','applied','acknowledged','recruiter_contacted',
    'interviewing','rejected','withdrawn','offered','accepted'
  )),
  resume_version_id TEXT,
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE INDEX idx_applications_company ON applications(company_id);
CREATE INDEX idx_applications_job ON applications(job_id);
CREATE INDEX idx_applications_applied ON applications(applied_at);

-- Append-only event log: the timeline is history, never rewritten.
CREATE TABLE application_events (
  id TEXT PRIMARY KEY,
  application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  data_json TEXT NOT NULL DEFAULT '{}'
) STRICT;

CREATE INDEX idx_application_events_app ON application_events(application_id, at);

CREATE TRIGGER application_events_no_update
BEFORE UPDATE ON application_events
BEGIN
  SELECT RAISE(ABORT, 'application_events is append-only');
END;

CREATE TRIGGER application_events_no_delete
BEFORE DELETE ON application_events
BEGIN
  SELECT RAISE(ABORT, 'application_events is append-only');
END;

CREATE TABLE application_outcomes (
  id TEXT PRIMARY KEY,
  application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN (
    'no_response','acknowledged','recruiter_contact','interview','rejection',
    'offer','withdrawn','accepted'
  )),
  occurred_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  proposed INTEGER NOT NULL DEFAULT 0,
  confirmed INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
) STRICT;

CREATE INDEX idx_outcome_app ON application_outcomes(application_id, occurred_at);

CREATE TABLE suppression_policies (
  id TEXT PRIMARY KEY,
  profile_id TEXT,
  policy_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE UNIQUE INDEX idx_suppression_policy_default
  ON suppression_policies(profile_id) WHERE profile_id IS NULL;

CREATE TABLE job_suppressions (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  application_id TEXT NOT NULL REFERENCES applications(id),
  state TEXT NOT NULL CHECK (state IN ('active','expired','restored')),
  reason TEXT NOT NULL,
  explanation TEXT NOT NULL,
  similarity REAL NOT NULL DEFAULT 0,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  policy_json TEXT NOT NULL DEFAULT '{}',
  expires_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (job_id, application_id)
) STRICT;

CREATE INDEX idx_suppressions_state ON job_suppressions(state);
CREATE INDEX idx_suppressions_job ON job_suppressions(job_id);

-- ``job_user_state`` already exists from 0004; M7 only starts writing it.
CREATE INDEX IF NOT EXISTS idx_job_user_state_state ON job_user_state(state);
