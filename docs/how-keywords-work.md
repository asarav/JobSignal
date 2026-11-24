# How keyword insights work

Job Signal reads the skills it recognises in each posting and stores them with
that posting revision, so demand can be described without re-reading the whole
database and without a button press.

## Where the data comes from

When a posting is assessed (`posting_intelligence.evaluate`, the normal crawl and
assessment pipeline, or a daily pass), the same pass extracts keywords from the
posting text and appends:

- one row in `job_keyword_revisions` per `(job, revision_hash)`, with the
  taxonomy version and the number of recognised keywords;
- one row in `job_keywords` per recognised skill in that revision.

Both tables are append-only: `BEFORE UPDATE` and `BEFORE DELETE` triggers abort.
A materially changed posting gets a new revision; the old revision is kept, and
insights read only the latest one per posting.

`keywords.backfill` (limit) is available for databases that were created before
this existed. It is resumable: it reports `processed` and `remaining`, and
running it again continues where it stopped. New data does not need it.

## Prevalence

`keywords.prevalence` accepts an optional `company_id` and returns:

| Field | Meaning |
|---|---|
| `scope` | `global` or `company` |
| `n_jobs` | postings in scope with a non-empty extraction |
| `min_sample` | threshold, 10 by default |
| `small_sample` | true when `n_jobs` is below the threshold |
| `keywords` | per skill: `jobs`, `prevalence`, Wilson `interval`, `insufficient` |
| `note` | a plain-language description of the sample rule |

Prevalence is `jobs mentioning the skill / n_jobs`. Below the threshold every row
has `insufficient: true` and the screen shows the posting count with no
percentage. A small count is never presented as a trend.

A posting with no readable text is **missing evidence**, not a zero: it is
excluded from `n_jobs` rather than counted as a posting that omits every skill.

Company scope resolves merges before counting, so an employer read under one
surviving company includes the postings of every company merged into it.

## Coverage

`keywords.coverage` compares the skills asked for in scope with your profile:

- imported and edited skills, plus per-skill statuses ("have experience",
  "not relevant");
- `coverage_ratio` = covered candidate skills / candidate skills, or `null` when
  there is nothing to compare;
- `covered` and `missing` rows carry the same counts and small-sample flag as
  prevalence.

Missing a skill is described as "asked for, not on your profile". It is not a
judgement about you or about the employer.

## In the app

The Keyword Insights screen shows the scope selector, a "How this works"
explanation, the prevalence table, and profile coverage. The same explanation is
linked from the screen rather than hidden in a tooltip, and the sample size is
always visible next to the table.

## Privacy

Keywords are derived from postings stored on this computer. Nothing is uploaded,
and no demographic or legal information is involved.

## Tests

- `tests/contract/test_company_identity_repositories.py` — repository parity,
  batched reads and immutability.
- `tests/integration/test_keyword_prevalence.py` — pipeline persistence,
  prevalence maths, small samples, missing ≠ negative, merge-aware company scope,
  coverage and resumable backfill.
- `tests/integration/test_ipc_company_keywords.py` — IPC round trips.
- `tests/integration/test_migrations_0015_0016.py` — upgrade from a 0014
  database.
