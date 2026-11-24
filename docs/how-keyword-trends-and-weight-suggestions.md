# How keyword trends and weight suggestions work

Two M10 surfaces describe what your own data shows. Neither predicts an
interview, and neither changes ranking without you accepting a change.

## Keyword trends

`keywords.trends` turns the stored keyword history into a time series. The
history is already per posting revision (`job_keyword_revisions` and
`job_keywords`), so a trend is a second reading of the same append-only rows —
no new table and no re-extraction.

### What is counted

For the jobs in scope, the service takes the **latest revision per job** and
groups it by the month (or ISO week) the revision was read. Each period reports:

| Field | Meaning |
|---|---|
| `period` | `YYYY-MM` for months, `YYYY-Www` for weeks |
| `n_jobs` | postings whose latest revision was read in that period |
| `keywords[].jobs` | postings in that period mentioning the keyword |
| `keywords[].mentions` | total mentions, including repeats inside one posting |
| `keywords[].prevalence` | `jobs / n_jobs`, or `null` below the threshold |
| `keywords[].interval` | Wilson 95% interval when a rate is shown |
| `insufficient` | true when the period is below `MIN_SAMPLE` (10 by default) |
| `no_data` | true when the period has no postings at all |

A period below the threshold keeps its counts and shows no share. A period with
no postings is reported as `no_data` rather than plotted as zero demand. A posting
with no readable text has no revision with keywords, so it is missing evidence
and never a non-mention.

### Scope and merges

The optional `company_id` goes through the same canonical resolution as
prevalence: a merged company is read under its surviving company, and the
postings of every company merged into it are included. The response echoes the
resolved `scope` and `company_id`.

### Reads

Two batched reads feed a whole response: the latest revision per job
(`latest_revisions_for_jobs`) and the keyword rows for those jobs
(`keyword_rows_for_jobs`). There is no query per keyword and no query per job;
a test asserts each source is read once.

The Keyword Insights screen renders the series as a month-by-keyword table with
the period's sample size and an explicit "count only", "insufficient data" or
"no postings" marker.

## Weight suggestions

`matching.get_weight_proposals` compares stored fit dimensions with the outcomes
you recorded:

1. Applications within the window that have a stored match result and a
   confirmed outcome are counted.
2. For each fit dimension, the mean stored score is computed separately for
   applications that got a reply and those that did not. A reply is one of the
   `RESPONSE_KINDS` in `app/application/analytics.py`; a recorded `no_response`
   is not a reply, and a missing outcome is not treated as evidence of anything
   about the posting.
3. If at least 20 applications are counted, at least 5 are on each side, and the
   largest observed gap is at least 0.05, a suggestion is returned.

The suggestion moves 0.05 of weight toward the dimension with the largest gap and
spreads the same amount proportionally over the other dimensions, then
normalizes. It reports the gap, the sample size, the two group means, and every
changed dimension, so the numbers can be checked. Otherwise
`insufficient_evidence` is true and `proposal` is `null` — but the observed
associations are still returned, each with its own `n` and `insufficient` flag.

These are associations in your own history. The screens never say a dimension
*caused* a reply and never present a rate as a chance of an interview.

### Accepting a change

`matching.apply_weight_proposal` is the only way a suggestion is applied, and the
screen asks for a second confirmation first. Accepting appends a
`fit_weight_versions` row with the new weights, the previous weights, the
supporting evidence and who accepted it, then updates the active weights. Rows
in `fit_weight_versions` are append-only (triggers abort updates and deletes), so
`matching.get_weight_history` can always show what ranking used before.

Editing weights by hand (`matching.update_weights`) records a version too,
marked `manual`. Reading a suggestion never writes anything.

## Tests

- `tests/integration/test_keyword_trends.py` — period counts, small-sample
  hiding, empty periods, merged scope, batched reads.
- `tests/integration/test_weight_proposals.py` — suggestion maths, insufficient
  sample, acceptance history, manual edits recorded, applications without a fit
  excluded.
- `tests/contract/test_fit_weight_repositories.py` — memory/SQLite parity and
  immutability.
- `tests/integration/test_ipc_m10.py` — IPC round trips and error envelopes.
- `tests/integration/test_migrations_0017.py` — upgrade from a 0016 database.

See [ADR-0014](adr/0014-associations-and-weight-history.md).
