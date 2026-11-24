-- M8 posting intelligence (spec §9.3-§9.7, §14.2).
--
-- These tables are history, not read models. A verdict the user was shown must
-- stay readable and re-derivable: assessments reference the exact signal rows
-- that produced them, and no row is ever updated or deleted in place.

CREATE TABLE posting_signals (
  signal_id TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  provider_version TEXT NOT NULL,
  default_weight REAL NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1))
) STRICT;

CREATE TABLE application_endpoint_observations (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  alias_id TEXT,
  observed_at TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN (
    'ok','closed_message','form_unavailable','gone_404_410',
    'generic_redirect','timeout','blocked','auth_required','error'
  )),
  http_status INTEGER,
  final_url TEXT,
  details_json TEXT NOT NULL DEFAULT '{}'
) STRICT;
CREATE INDEX idx_endpoint_job ON application_endpoint_observations(job_id, observed_at);

CREATE TABLE signal_observations (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  signal_id TEXT NOT NULL REFERENCES posting_signals(signal_id),
  provider_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('available','missing','error')),
  risk_contribution REAL,
  confidence REAL NOT NULL,
  strong INTEGER NOT NULL DEFAULT 0 CHECK (strong IN (0,1)),
  evidence_json TEXT NOT NULL DEFAULT '[]',
  evidence_source TEXT,
  observed_at TEXT,
  evaluated_at TEXT NOT NULL,
  missing_reason TEXT,
  explanation TEXT NOT NULL
) STRICT;
CREATE INDEX idx_sigobs_job ON signal_observations(job_id, evaluated_at);

CREATE TABLE risk_model_versions (
  id TEXT PRIMARY KEY,
  model_id TEXT NOT NULL,
  model_version TEXT NOT NULL,
  config_json TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  description TEXT,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT 'system',
  UNIQUE (model_id, model_version, config_hash)
) STRICT;

CREATE TABLE risk_assessments (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  risk_model_version_id TEXT NOT NULL REFERENCES risk_model_versions(id),
  risk REAL NOT NULL,
  confidence REAL NOT NULL,
  class TEXT NOT NULL CHECK (class IN (
    'likely_active','needs_verification','likely_stale','likely_inactive'
  )),
  evidence_sufficiency TEXT NOT NULL CHECK (evidence_sufficiency IN (
    'sufficient','limited','insufficient'
  )),
  signal_observation_ids_json TEXT NOT NULL,
  top_contributors_json TEXT NOT NULL DEFAULT '[]',
  evaluated_at TEXT NOT NULL,
  -- The inputs that produced this verdict. A repeat evaluation with the same
  -- inputs reuses the row instead of appending a duplicate (spec §17.3).
  input_hash TEXT NOT NULL DEFAULT ''
) STRICT;
CREATE INDEX idx_risk_job ON risk_assessments(job_id, evaluated_at);
CREATE INDEX idx_risk_class ON risk_assessments(class, evaluated_at);
CREATE INDEX idx_risk_input ON risk_assessments(job_id, input_hash);

CREATE TABLE classification_changes (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  from_class TEXT,
  to_class TEXT NOT NULL,
  from_assessment_id TEXT REFERENCES risk_assessments(id),
  to_assessment_id TEXT NOT NULL REFERENCES risk_assessments(id),
  changed_at TEXT NOT NULL
) STRICT;
CREATE INDEX idx_class_changes_job ON classification_changes(job_id, changed_at);

CREATE TABLE company_hiring_summaries (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id),
  computed_at TEXT NOT NULL,
  sample_tier TEXT NOT NULL CHECK (sample_tier IN ('insufficient','limited','adequate')),
  metrics_json TEXT NOT NULL,
  labels_json TEXT NOT NULL DEFAULT '[]',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  methodology_version TEXT NOT NULL
) STRICT;
CREATE INDEX idx_chs_company ON company_hiring_summaries(company_id, computed_at);

-- Append-only enforcement lives in the database, not only in the repository:
-- one careless UPDATE would otherwise rewrite the evidence a verdict cites.
CREATE TRIGGER trg_signal_observations_immutable_update
BEFORE UPDATE ON signal_observations
BEGIN
  SELECT RAISE(ABORT, 'signal_observations is append-only');
END;

CREATE TRIGGER trg_signal_observations_immutable_delete
BEFORE DELETE ON signal_observations
BEGIN
  SELECT RAISE(ABORT, 'signal_observations is append-only');
END;

CREATE TRIGGER trg_risk_assessments_immutable_update
BEFORE UPDATE ON risk_assessments
BEGIN
  SELECT RAISE(ABORT, 'risk_assessments is append-only');
END;

CREATE TRIGGER trg_risk_assessments_immutable_delete
BEFORE DELETE ON risk_assessments
BEGIN
  SELECT RAISE(ABORT, 'risk_assessments is append-only');
END;

CREATE TRIGGER trg_risk_model_versions_immutable_update
BEFORE UPDATE ON risk_model_versions
BEGIN
  SELECT RAISE(ABORT, 'risk_model_versions is append-only');
END;

CREATE TRIGGER trg_risk_model_versions_immutable_delete
BEFORE DELETE ON risk_model_versions
BEGIN
  SELECT RAISE(ABORT, 'risk_model_versions is append-only');
END;

CREATE TRIGGER trg_classification_changes_immutable_update
BEFORE UPDATE ON classification_changes
BEGIN
  SELECT RAISE(ABORT, 'classification_changes is append-only');
END;

CREATE TRIGGER trg_classification_changes_immutable_delete
BEFORE DELETE ON classification_changes
BEGIN
  SELECT RAISE(ABORT, 'classification_changes is append-only');
END;

CREATE TRIGGER trg_company_hiring_summaries_immutable_update
BEFORE UPDATE ON company_hiring_summaries
BEGIN
  SELECT RAISE(ABORT, 'company_hiring_summaries is append-only');
END;

CREATE TRIGGER trg_company_hiring_summaries_immutable_delete
BEFORE DELETE ON company_hiring_summaries
BEGIN
  SELECT RAISE(ABORT, 'company_hiring_summaries is append-only');
END;

CREATE TRIGGER trg_endpoint_observations_immutable_update
BEFORE UPDATE ON application_endpoint_observations
BEGIN
  SELECT RAISE(ABORT, 'application_endpoint_observations is append-only');
END;

CREATE TRIGGER trg_endpoint_observations_immutable_delete
BEFORE DELETE ON application_endpoint_observations
BEGIN
  SELECT RAISE(ABORT, 'application_endpoint_observations is append-only');
END;
