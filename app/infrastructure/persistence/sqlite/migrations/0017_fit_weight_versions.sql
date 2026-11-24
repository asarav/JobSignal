-- M10: append-only history of accepted fit weights.
--
-- Accepting a weight proposal (or editing weights by hand) writes one row here
-- before the active weights change. Rows are never updated or deleted, so the
-- weighting that produced an earlier ranking can always be recovered from
-- `previous_weights_json`. Proposals themselves are computed on read; only an
-- explicit acceptance is stored.

CREATE TABLE fit_weight_versions (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  weights_json TEXT NOT NULL,
  previous_weights_json TEXT,
  source TEXT NOT NULL CHECK (source IN ('manual', 'proposal')),
  evidence_json TEXT NOT NULL DEFAULT '{}',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL
) STRICT;

CREATE INDEX idx_fit_weight_versions_profile
  ON fit_weight_versions(profile_id, created_at DESC);

CREATE TRIGGER fit_weight_versions_no_update
BEFORE UPDATE ON fit_weight_versions
BEGIN
  SELECT RAISE(ABORT, 'fit_weight_versions is append-only');
END;

CREATE TRIGGER fit_weight_versions_no_delete
BEFORE DELETE ON fit_weight_versions
BEGIN
  SELECT RAISE(ABORT, 'fit_weight_versions is append-only');
END;
