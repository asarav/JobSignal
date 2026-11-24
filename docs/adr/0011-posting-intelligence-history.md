# ADR-0011: Posting-intelligence history is append-only and re-derivable

**Status:** Accepted (Milestone 8)

## Context

Posting risk is a claim the app makes to a job seeker about an employer. The
specification requires that every verdict be explainable (§9.1 rule 4),
reproducible from stored signals plus model version and configuration (§9.6),
and never rewritten (§9.1 rule 8, §14.4). We also need the current verdict per
job for ranking and list views, cheaply, over a library that can reach 100k jobs.

Two decisions follow from that:

1. How to store signals and assessments.
2. How to read "the latest verdict" without an extra table to keep in sync.

## Options considered

**Storage**

- *Mutable current-state row per job.* Simple reads, but it destroys the history
  the user was shown and makes reproducibility impossible.
- *Append-only rows plus database triggers.* History is preserved, an accidental
  `UPDATE` fails loudly, and each assessment can point at the exact signals it
  consumed. Costs a little storage and requires "latest" reads.
- *Append-only rows enforced only in the repository.* Cheaper to write, but one
  direct SQL statement (a migration, a debugging session) could rewrite evidence.

**Latest-verdict reads**

- *A maintained `job_current` read model*, updated in the same transaction.
  Fast, but a second source of truth that can drift and must be rebuilt.
- *An indexed `ORDER BY evaluated_at DESC, rowid DESC LIMIT 1`* per job, plus a
  batched join for pages. One source of truth; the index makes it cheap.

## Decision

- `signal_observations`, `risk_assessments`, `risk_model_versions`,
  `classification_changes`, `company_hiring_summaries` and
  `application_endpoint_observations` are append-only. SQLite triggers abort
  `UPDATE` and `DELETE`; repositories expose no mutating method.
- An assessment stores the ids of its signal rows, the model-version row and a
  SHA-256 config hash. Re-running the pure model over the stored signals with the
  same configuration reproduces the numbers exactly (golden test).
- Each assessment also stores an `input_hash` over the job content hash,
  snapshot hashes, endpoint states, repost events, company summary and
  configuration. A re-evaluation with identical inputs reuses the row instead of
  appending a duplicate.
- No `job_current` table. "Latest" is an indexed per-job query, batched with an
  `IN (...)` join for list pages. We will add a read model only if benchmarks
  show the indexed read cannot meet the §17.1 budget, and it will be rebuildable.

## Consequences

- Storage grows with history, as intended: roughly one assessment and eight
  signal rows per changed job per evaluation. Company summaries are capped at
  one per company per day.
- A weight or threshold change creates a new `risk_model_versions` row. Old
  assessments remain readable and correctly point at the configuration they
  used.
- Deleting history is impossible through the application. "Treat as active" is a
  display preference on the job's user state, not an edit to risk.
- Any future migration that needs to correct history must add a column or a new
  table; it cannot rewrite the old rows.
