# How discovery works

Job Signal is built on one idea: **jobs find you**, not the other way round.
You should not have to know which companies are hiring, hunt for their careers
page, or paste board URLs. Everything below happens on your machine, without an
account and without asking you first.

## The continuous pass

Discovery keeps running. When one bounded pass finishes, the worker pauses for
`discovery.auto_discover_interval_minutes` (default 60) and starts the next, so
new boards and postings keep arriving and every board already registered is
re-checked as its interval comes due. One pass:

1. **Ask every enabled discovery provider for candidate career sites.**
   `discovery.web_index` queries a public web URL index for fixed-host ATS
   boards (`host/{tenant}`). `discovery.domain_index` covers the shapes a
   prefix query cannot reach: Workday, iCIMS, BambooHR and Eightfold tenants on
   their own subdomains, and UKG Pro/Workable boards whose employer sits in the
   path.
   Neither uses a fixed list of employers. Each web-index query asks for a
   large block of rows (up to 10,000 from the archive fallback) so one answer
   covers a whole slice of the tenant space.
2. **Rotate what it looks at.** One pass covers a bounded set of slices of the
   tenant space — for example, ATS tenants starting with `s` on Greenhouse and
   a dozen neighbouring slices, queried in parallel. The next pass covers
   different slices, so coverage grows with every pass and eventually wraps
   around to refresh what was seen before. A day's index work is also capped
   (`discovery.max_index_slices_per_day`, default 288 = 24 hourly passes), so a
   much shorter pause cannot turn continuous discovery into a crawl of the
   public indexes; the remaining passes still crawl what is due and resume the
   index tomorrow.
3. **Register what is new.** Candidates are deduplicated against the sources
   already stored, and up to `discovery.max_new_sources_per_run` (default 200)
   new career sites are added, each with its own crawl schedule.
4. **Harvest posting URLs from the same index answers.** The funnel provider
   reads posting URLs out of the slice rows board discovery already fetched
   (`boards.greenhouse.io/<tenant>/jobs/<id>`), resolves each one to the board
   it belongs to (including a board registered on a sibling host), verifies it
   with a single detail fetch, and stores it with "via index" provenance — up
   to `discovery.funnel_postings_per_pass` per pass, spread round-robin across
   boards rather than spent on the first tenant in the index answer. A
   long-tail company can show a relevant posting without ever paying for a
   full board crawl. A later full listing of that board clears the badge when
   it finds the posting unchanged; if the content changed, the listing's own
   record replaces the index-verified one.
5. **Rank sources into tiers.** Sources are rated against your profile, skills
   and the hard filters of your search profiles: **hot** (hourly), **warm**
   (daily) and **cold** (weekly). Only a tier never crawls more often than its
   interval; the tier and its reason are shown on the Discover screen and
   changes are evented. Without a profile — or after its skills, titles and
   filters are cleared — any tier assigned earlier is removed and discovery
   keeps the plain due-based cadence below.
6. **Crawl what is due.** Sources that have never been crawled, or whose
   interval (narrowed by their tier) has elapsed, are crawled stalest-first
   and in parallel, up to `discovery.max_crawl_sources_per_run` (default 60)
   per pass and bounded by the global crawl concurrency cap. Harvested URLs
   never drive `not_seen`/removal: only a full listing can prove a job is gone.
7. **Store the outcome.** Every crawl writes a `crawl_runs` row and publishes
   `crawl.completed` / `crawl.failed`. New jobs land in the database and are
   immediately searchable through your search profiles, with their hard
   filters applied.
8. **Cold start.** A fresh install has no run state, so the worker runs
   `discovery.warmup_passes` (default 5) passes back-to-back (each still
   bounded by the pass caps and the crawl gate) to register most of the bundled
   seed list within minutes — at most 200 new sources per pass. Because the
   seed list is ordered largest-first, the biggest employers arrive first.
   The loop stops early once a pass registers nothing new; afterwards the
   normal continuous cadence takes over.

Each enabled source is also kept on its own loop at its effective interval —
narrowed by its relevance tier — so boards and their postings are re-checked,
updated when they change, and marked removed after repeated full listings no
longer contain them. Both paths share the registry and a due check, so a board
that one just refreshed is not fetched again by the other. A restart of the app
does not reset any of it: the last run time, the rotation cursor and every
source schedule are stored in the app database.

## Sources, not seeds

`discovery.ats_directory` ships an offline seed list of 25,716 boards:
Greenhouse, Lever, Ashby, SmartRecruiters, Workable, BambooHR, Workday, iCIMS
and UKG Pro (UltiPro), each confirmed live through the board's own public ATS API. It exists so a brand new install has
something to crawl immediately (and so tests do not need the network). It is
**not** the discovery engine: the app never depends on that file to find
employers, and the file going stale only removes a head start, not discovery.
`scripts/build_seed.py --refresh` re-verifies and re-ranks it.

Harvesting that list is done in the bulk web indexes, in 10,000-row slices.
Board discovery keeps one row per board — per tenant, except Workday, where a
tenant runs one board per career site; the funnel separately reads the
job-detail URLs in the same rows and verifies them individually. A single
Greenhouse slice that returned 1190 rows used to yield roughly 100 tenants and
now yields about 800. A slice that
comes back completely full is walked again as narrower buckets, and each
completed slice (and each UKG Pro chunk) is checkpointed, so an interrupted run
resumes where it stopped instead of starting over. A later expansion pass runs
with `--incremental`: it verifies only boards no earlier pass has settled and
merges them into the shipped file, so growing the list costs one pass over the
new boards rather than a re-check of every board already in it. Every index
request, chunk and verification outcome is also written to
`.seed_build_log.jsonl` (one JSON object per event, `--log-file` to move it),
with status, body size and elapsed time, so a run that failed unattended can
be explained afterwards instead of guessed at. Every candidate collected
this way still has to answer on its own ATS API before it is written to the
file.

You can still add a source yourself if you know a company you want tracked, and
you can disable any source or any provider. Removing your involvement is the
point, but it is never a requirement.

## What leaves your machine

Discovery contacts public endpoints and nothing else. There is no account, no
telemetry and no cloud database.

| Provider | Contacted | Sent |
|---|---|---|
| `discovery.web_index` | Common Crawl's public index (`index.commoncrawl.org`), then the Internet Archive CDX API (`web.archive.org`) as fallback | an ATS host and a single leading character, e.g. `boards.greenhouse.io/s` |
| `discovery.domain_index` | the Internet Archive CDX API (`web.archive.org`) | an ATS domain and a single leading character, e.g. `myworkdayjobs.com` filtered to hosts starting with `a`; also covers `eightfold.ai` |
| `discovery.oracle_recruiting` | the Internet Archive CDX API (`web.archive.org`) | one Oracle HCM pod, filtered to Candidate Experience URLs |
| `discovery.ats_directory` | nothing | nothing |
| crawl of a registered board | that ATS's public job pages | ordinary page requests |

The crawler also refuses anything that is not a public `http(s)` URL. Loopback,
private and link-local address literals (`127.0.0.1`, `169.254.169.254`,
`10.x`, `192.168.x`, `[::1]`), their numeric aliases, `localhost`-style names
and non-http schemes are rejected before a request is sent, and every redirect
hop is checked again. There is no setting that turns this off; the test-only
override is a constructor argument used by fixture servers.

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
- **Subdomain and path discovery is slower.** Workday, iCIMS, BambooHR,
  Eightfold, UKG Pro and Workable boards are enumerated by
  `discovery.domain_index`, but a
  domain-wide index query takes 40–90 s, so one pass walks only three slices
  (three leading-character buckets) instead of the twelve fixed-host slices.
  Coverage therefore grows more slowly, and it still grows with every pass
  without a seed refresh. A slice the archive throttles or times out is logged and
  re-walked after the rotation wraps; the pass does not fail.
- **Oracle Recruiting Cloud is discovered pod by pod.** Its career sites live
  at `{tenant}.fa.{region}.oraclecloud.com/hcmUI/CandidateExperience/...`.
  Certificate transparency cannot enumerate the tenants (the pods use wildcard
  certificates) and a whole-`oraclecloud.com` index query times out, but a
  query scoped to one HCM pod answers in 17–38 s and returns full career-site
  URLs. `discovery.oracle_recruiting` therefore rotates a list of pods, two per
  pass, and reconstructs each board from the URL — no manual URL is required.
  A throttled or retired pod is logged and retried after the rotation wraps.
- **Wrapped and custom career sites use the generic layer.** A company that
  runs Beamery, Radancy, Clinch, SmashFly or another marketing front end is
  read by `ats.career_site`: it looks for an embedded Workday, iCIMS, Oracle
  Recruiting Cloud, Taleo, Eightfold or Phenom host and delegates to that
  connector; otherwise it harvests public job links from the page and sitemap
  and runs each detail through the extraction pipeline (including JSON-LD).
  This is also the Google/Meta path: no internal gHire or GraphQL endpoint is
  used, and a page that exposes no public postings stays a manual-URL source.
  Wrapper and custom sites are not enumerated from the web index themselves;
  add one by URL, or let the optional company-name/search providers find it.
- **A pass is deliberately bounded.** With thousands of sources, each one is
  spread across passes by its tier and interval rather than fetched all at
  once; the per-pass caps and the global crawl gate decide the pace.
- **Upstream schema drift fails loudly.** The JSON connectors (Greenhouse,
  Ashby, SmartRecruiters, Workday, Eightfold, Phenom, Amazon) expect their
  endpoint's posting list. If it disappears or changes shape, the connector
  raises `connector.schema_drift`, the crawl is recorded as failed, and no
  stored posting from that source is marked removed. Workday detail pages
  additionally fall back to the public posting HTML, so JSON-LD/static
  extraction can still recover the job.
- **Dead boards stay visible.** A source whose crawl keeps failing is not
  silently deleted: it shows up as failed crawl runs so you can remove it.

More sources mean more jobs to filter — the app's job is to do that filtering
and ranking for you, so you only look at opportunities that fit.

## Tuning it

All of these live under `discovery` in the app configuration (Settings →
advanced) and are validated when saved:

| Key | Default | Meaning |
|---|---|---|
| `auto_discover_interval_minutes` | `60` | Pause between automatic passes; lower means more continuous coverage |
| `auto_discover_interval_hours` | — | Legacy fallback used when minutes is not set |
| `max_new_sources_per_run` | `200` | New sources registered per pass |
| `max_crawl_sources_per_run` | `60` | Sources crawled per pass (oldest attempt first) |
| `cursor_step` | `12` | Index slices walked per pass (`discovery.domain_index` walks three of them, because domain queries are slower) |
| `max_index_slices_per_day` | `288` | Daily ceiling on index slices across every pass; `0` pauses index discovery while crawling continues |
| `web_index_limit` | `200` | Rows requested per Common Crawl query |
| `web_index_cdx_limit` | `10000` | Rows requested per CDX query (one whole bucket) |
| `warmup_passes` | `5` | Passes run back-to-back on a cold start (`0`/`1` disables) |
| `funnel_enabled` | `true` | Harvest and verify posting URLs; disabling it restores plain due-based crawling |
| `funnel_postings_per_pass` | `50` | Posting URLs verified per pass (one fetch each) |
| `tier_intervals_s` | `[3600, 86400, 604800]` | Minimum crawl spacing for hot / warm / cold sources |

To stop a specific provider or source, disable it in Settings → Sources /
Plugins; nothing else changes.
