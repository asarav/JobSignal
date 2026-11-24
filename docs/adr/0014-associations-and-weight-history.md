# ADR-0014 — Outcome associations never auto-apply; accepted weightings are append-only

- Status: accepted
- Date: 2026-11-23
- Context: M10 (keyword trends, outcome-driven weight suggestions, risk surfaces)

## Context

Applications have recorded outcomes, and each stored match has per-dimension
scores. It is tempting to feed those back into ranking automatically. That would
turn a small, self-selected sample into silent behaviour change: the user would
not know their weights had moved, could not see the evidence, and could not get
the previous ordering back. It would also invite causal and predictive language
the product avoids.

Keywords have a similar constraint: `job_keyword_revisions` is append-only and
per posting revision, so a trend must be a reading of that history rather than a
new mutable summary that could drift from it.

## Decision

### Weight changes are suggestions with explicit acceptance

`matching.get_weight_proposals` computes group means of stored dimension scores
for applications with a reply and without one, using the outcome rules already
defined for analytics. A suggestion is produced only at 20+ applications with at
least 5 per side and an observed gap of at least 0.05; otherwise the response says
`insufficient_evidence` and returns no proposal. The observed associations are
always returned with their `n` and `insufficient` flags, so a small sample is
visible rather than hidden.

The suggestion's magnitude is fixed and bounded (0.05 toward the dimension with
the largest gap, spread proportionally elsewhere, then normalized). It is
explainable: every changed dimension shows the previous and new weight, and the
gap and sample size are part of the payload.

Applying a suggestion requires `matching.apply_weight_proposal`, which the UI
gates behind a confirmation step. Reading a suggestion writes nothing.

### Accepted weightings live in an append-only table

`fit_weight_versions` (migration 0017) stores `weights_json`,
`previous_weights_json`, `source` (`manual` or `proposal`), `evidence_json`,
`created_by` and `created_at`. `BEFORE UPDATE` and `BEFORE DELETE` triggers abort,
so history cannot be rewritten. Both acceptance and a manual weight edit append a
row before the active weights change; `matching.get_weight_history` returns the
rows newest first, and the first row has `previous_weights = null` meaning the
built-in default.

The active weights remain in the existing `scoring_configurations` row because
matching reads it on every run. The history table is the audit trail, and a prior
weighting is recoverable from the `previous_weights_json` of the row that
replaced it.

### Trends read the existing per-revision history

`keywords.trends` groups the latest revision per job by the period it was read and
reports counts and shares per keyword. No new storage is introduced: a trend is a
projection of `job_keyword_revisions` / `job_keywords`, so it cannot diverge from
prevalence by construction. Periods below `MIN_SAMPLE` keep counts and hide rates;
periods with no postings are `no_data`, never zero demand. A posting with no
extractable text has no keyword revision, so it is missing evidence, not a
negative.

## Consequences

- The user sees the sample and the evidence before accepting, and can compare the
  new weighting with the previous one afterwards.
- No background process can change weights; only an IPC call the user triggers
  can.
- The wording stays associational: no causal claim, no probability of an
  interview, and no certainty language.
- Trend data can never disagree with prevalence because both read the same rows.
- Trend charts remain a table-backed series for now (no charting dependency was
  added); the payload is chart-ready if a later milestone wants a graphic.
