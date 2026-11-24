# ADR-0013 — Company merges are a decision log; keywords are per posting revision

- Status: accepted
- Date: 2026-11-23
- Context: M9 breadth (company merge/unmerge, keyword demand)

## Context

Two records can describe the same employer ("Acme" and "Acme, Inc."), and a
company's posting counts are only useful if both are read together. At the same
time, every historical count and hiring summary must stay reproducible, so a
merge cannot rewrite the rows behind them.

Keyword demand has the mirror problem: `keywords.extract` worked on one text at
a time, so demand across postings was never stored and could not be described
without re-reading every posting.

## Decision

### Company merges are append-only decisions

`company_merges` stores one row per decision: `survivor_id`, `merged_id`,
`action` (`merge` or `unmerge`), `reason`, `created_by` and `created_at`.
`BEFORE UPDATE` and `BEFORE DELETE` triggers abort, so a decision is never
rewritten or removed. The `companies` table itself is never modified by a merge.

The canonical company is computed when evidence is read, not written: take the
latest decision per `merged_id` and follow the chain to a survivor, with a cycle
guard in `app/domain/services/company_resolution.py`. A merge whose survivor is
itself merged therefore resolves to the final survivor. Unmerge appends an
`unmerge` decision; because no underlying row changed, the previous counts,
summaries and posting lists return exactly.

A merge is idempotent (merging an already-merged pair records nothing) and a
merge that would create a cycle is rejected with `validation.merge_cycle`.
Summaries are only ever written for the surviving company; a merged child never
gains a summary of its own.

### Keywords belong to a posting revision

`job_keyword_revisions` stores one revision per `(job_id, revision_hash)` and
`job_keywords` stores the recognised skills for that revision, append-only with
`BEFORE UPDATE`/`BEFORE DELETE` triggers. Prevalence reads the *latest* revision
per job with batched queries — never one query per posting.

Prevalence's denominator is the number of postings in scope with a non-empty
extraction. A posting with no readable text is missing evidence, not evidence of
zero demand, so it is excluded rather than counted as a non-mention. Below
`MIN_SAMPLE = 10` postings every row is marked `insufficient` and the rate is
hidden in the UI; counts are still shown, because a count is not a trend.

Coverage compares the top keywords in scope with the profile's skills (imported
skills, overrides and skill statuses) and reports covered and missing skills plus
`n` and the same small-sample rule.

Keywords are written by the normal assessment pass (`RiskEvaluationService`),
before input-hash reuse, so they fill as postings are evaluated. `backfill` is a
resumable helper for databases that predate this migration; it is not required
for new data.

## Consequences

- Company evidence is always read through the canonical map: list, detail, job
  rows, summaries and analytics.
- Undoing a merge is a new row, so the audit trail shows both decisions and the
  current state is `latest decision per merged_id`.
- Re-reading a posting revision does not duplicate keywords; a changed revision
  keeps the old one for history and becomes the one prevalence uses.
- A very small database shows counts but no percentages until it grows, which is
  the intended honest behaviour.
- Trend charts and outcome-driven weight proposals remain out of scope (M10).
