# How discovery works

Job Signal is built on one idea: **jobs find you**, not the other way round.
You should not have to know which companies are hiring, hunt for their careers
page, or paste board URLs. Everything below happens on your machine, without an
account and without asking you first.

## The daily pass

Once a day the worker runs a discovery pass:

1. **Ask every enabled discovery provider for candidate career sites.**
   The built-in provider (`discovery.web_index`) queries a public web URL
   index for real ATS boards. It does not use a fixed list of employers.
2. **Rotate what it looks at.** One pass covers a bounded set of slices of the
   tenant space — for example, ATS tenants starting with `s` on Greenhouse and
   a dozen neighbouring slices, queried in parallel. The next pass covers
   different slices, so coverage grows every day and eventually wraps around
   to refresh what was seen before.
3. **Register what is new.** Candidates are deduplicated against the sources
   already stored, and up to `discovery.max_new_sources_per_run` (default 200)
   new career sites are added, each with its own crawl schedule.
4. **Crawl what is due.** Sources that have never been crawled, or whose own
   interval has elapsed, are crawled stalest-first and in parallel, up to
   `discovery.max_crawl_sources_per_run` (default 60) per pass and bounded by
   the global crawl concurrency cap.
5. **Store the outcome.** Every crawl writes a `crawl_runs` row and publishes
   `crawl.completed` / `crawl.failed` events.
6. **Cold start.** A fresh install has no run state, so the worker runs
   `discovery.warmup_passes` (default 5) passes back-to-back (each still
   bounded by the pass caps and the crawl gate) to register the bundled
   ~1,000-board seed list within minutes. It stops early once a pass
   registers nothing new; afterwards only the normal daily pass runs.
5. **Store the outcome.** Every crawl writes a `crawl_runs` row and publishes
   `crawl.completed` / `crawl.failed`. New jobs land in the database and are
   immediately searchable through your search profiles, with their hard
   filters applied.

The pass repeats the next day, and again after that. A restart of the app does
not reset it: the last run time and the rotation cursor are stored in the app
database, so the schedule and the covered ground both survive.

## Sources, not seeds

`discovery.ats_directory` ships a small offline seed list. It exists so a brand
new install has something to crawl immediately (and so tests do not need the
network). It is **not** the discovery engine: the app never depends on that
file to find employers, and the file going stale only removes a head start, not
discovery.

You can still add a source yourself if you know a company you want tracked, and
you can disable any source or any provider. Removing your involvement is the
point, but it is never a requirement.

## What leaves your machine

Discovery contacts public endpoints and nothing else. There is no account, no
telemetry and no cloud database.

| Provider | Contacted | Sent |
|---|---|---|
| `discovery.web_index` | `web.archive.org` (public URL index query API) | an ATS host and a single leading character, e.g. `boards.greenhouse.io/s` |
| `discovery.ats_directory` | nothing | nothing |
| crawl of a registered board | that ATS's public job pages | ordinary page requests |

There is no personal data in any of those requests: no resume, no name, no
search history, and no search-profile text. The current build ships no
search-engine provider; if one is enabled later it stays behind
`features.search_engine_discovery` (default off) because it would be the only
provider that sends your own query text to a third party, and Settings →
Privacy must list it explicitly before it can be turned on.

## Coverage, honestly

Discovery is not a complete view of the job market, and the app never claims to
be:

- **The index lags.** A company that appears today may take weeks to be picked
  up, and one that just disappeared may still show up as a source until its
  crawl fails.
- **Some ATS hosts are invisible.** Only hosts with public job boards can be
  enumerated. Companies that host jobs on their own site are found later, via
  the user-URL, company-name, sitemap and optional search-engine providers.
- **A pass is deliberately bounded.** With hundreds of sources, each one is
  visited over several days rather than all at once; your per-source schedules
  decide the order.
- **Dead boards stay visible.** A source whose crawl keeps failing is not
  silently deleted: it shows up as failed crawl runs so you can remove it.

More sources mean more jobs to filter — the app's job is to do that filtering
and ranking for you, so you only look at opportunities that fit.

## Tuning it

All of these live under `discovery` in the app configuration (Settings →
advanced) and are validated when saved:

| Key | Default | Meaning |
|---|---|---|
| `auto_discover_interval_hours` | `24` | How often the automatic pass runs |
| `max_new_sources_per_run` | `50` | New sources registered per pass |
| `max_crawl_sources_per_run` | `40` | Sources crawled per pass (oldest attempt first) |
| `web_index_limit` | `60` | Tenant URLs requested per index query |
| `web_index_lookback_days` | `540` | Ignore index captures older than this |

To stop a specific provider or source, disable it in Settings → Sources /
Plugins; nothing else changes.
