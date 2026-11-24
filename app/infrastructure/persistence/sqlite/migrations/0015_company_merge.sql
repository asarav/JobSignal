-- M9: company merge/unmerge with an append-only audit.
--
-- The companies table is never mutated. A merge is a decision recorded here, and
-- every read resolves a merged company to its survivor by walking this log from
-- the newest row. Unmerge appends a further decision, so the earlier rows stay
-- intact and the prior view is restored exactly.

CREATE TABLE company_merges (
  id TEXT PRIMARY KEY,
  survivor_id TEXT NOT NULL REFERENCES companies(id),
  merged_id TEXT NOT NULL REFERENCES companies(id),
  action TEXT NOT NULL CHECK (action IN ('merge', 'unmerge')),
  reason TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL,
  CHECK (survivor_id <> merged_id)
) STRICT;

--: Newest decision per merged company, used by the canonical resolution read.
CREATE INDEX idx_company_merges_merged ON company_merges(merged_id, created_at DESC);
CREATE INDEX idx_company_merges_survivor ON company_merges(survivor_id, created_at DESC);

CREATE TRIGGER company_merges_no_update
BEFORE UPDATE ON company_merges
BEGIN
  SELECT RAISE(ABORT, 'company_merges is append-only');
END;

CREATE TRIGGER company_merges_no_delete
BEFORE DELETE ON company_merges
BEGIN
  SELECT RAISE(ABORT, 'company_merges is append-only');
END;
