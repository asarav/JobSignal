# How company intelligence works

The Companies screen is descriptive. It shows what has been observed for a
company, what you have done there, and — only when the sample supports it — a
hiring summary. It never labels a company as bad, and it never turns a small
sample into a conclusion.

## What each screen shows

`companies.list` returns one row per known company with:

- the canonical name and domain,
- total and active postings seen,
- how many applications you have tracked there,
- when a posting from that company was last seen.

`companies.get_detail` returns the newest postings for the company (bounded to
200), the latest risk assessment for each, the number of reposts observed, the
latest company hiring summary, and the applications you tracked there.

## Hiring summaries

Summaries are produced by `CompanySummaryService` at most once per company per
day and are stored append-only in `company_hiring_summaries`. Labels follow the
sample tier:

| Tier | Condition | What is shown |
|---|---|---|
| insufficient | under 3 roles, or window under 21 days | raw counts only |
| limited | 3–7 roles, or window under 60 days | counts, no labels |
| adequate | at least 8 roles and a 60-day window | cautious labels |

A label describes an observation ("repeated reposting observed"), never an
intent. There is no "scam", "fake" or "deceptive" wording anywhere.

## Merging companies

Two records that describe the same employer can be merged with
`companies.merge` (`company_id` into `into_id`, optional `reason`). A merge is a
decision, not an edit: `company_merges` stores `survivor_id`, `merged_id`, the
action (`merge` or `unmerge`), the reason and who recorded it. Triggers abort any
update or delete, so the audit trail is append-only.

Every company read resolves the merge first. `companies.list` folds the merged
company's postings and applications into the survivor and reports `merged_ids` and
`merged_company_count`; `companies.get_detail` on a merged id returns the survivor
with `requested_id` set; job rows and hiring summaries also read under the
survivor. Summaries are only written for survivors. A merge that would create a
cycle is rejected (`validation.merge_cycle`), and repeating a merge records
nothing.

`companies.unmerge` appends an `unmerge` decision. Because no company row and no
count was ever rewritten, the earlier view returns exactly. Merging twice or
undoing an already-separated company is a no-op. `companies.get_merge_history`
returns the decisions for one company (or all), newest first, plus the current
`currently_merged` map.

The Companies screen has an explicit review-and-confirm step before a merge, shows
the audit trail with the reason and time, and offers an undo on decisions that are
still current. Nothing is submitted, clicked or pressed on a job board.

Merging never changes risk history, snapshots or posting rows; it only changes how
company evidence is read. See
[ADR-0013](adr/0013-company-merge-and-keyword-history.md).

## Keyword demand

Per-posting keyword persistence and prevalence live in migration 0016 and are
described in [how-keywords-work.md](how-keywords-work.md).

## Tests

- `tests/integration/test_companies.py` — counts, detail contents and the
  not-found error.
- `tests/integration/test_company_merge.py` — merge, unmerge, idempotency, cycle
  rejection, canonical reads and summary placement.
- `tests/contract/test_company_identity_repositories.py` — memory/SQLite parity
  and append-only triggers.
