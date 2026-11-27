# How discovery works

Job Signal is built on one idea: **jobs find you**, not the other way round.
You should not have to know which companies are hiring, hunt for their careers
page, or paste board URLs. Everything below happens on your machine, without an
account and without asking you first.

## The continuous pass

Discovery starts with the app: the first pass runs as soon as the worker is
ready, and a startup import registers the whole bundled seed catalogue in the
background (below), so opening Job Signal begins filling the corpus instead of
waiting for a grace period. Discovery then keeps running. When one bounded pass
finishes, the worker pauses for `discovery.auto_discover_interval_minutes`
(default 60) and starts the next, so new boards and postings keep arriving and
every board already registered is re-checked as its interval comes due. The startup seed import runs before that first pass, and a
dedicated backlog drain then crawls every never-attempted board continuously:
it selects batches larger than the crawl gate and starts the next batch
immediately, so the gate stays saturated and the per-origin rate limiter (not
a timer) sets the pace.  The normal cadence starts once the backlog is empty.
One pass:

1. **Ask every enabled discovery provider for candidate career sites.**
   `discovery.web_index` queries a public web URL index for fixed-host ATS
   boards (`host/{tenant}`), including Rippling's `ats.rippling.com/{slug}` and
   `{slug}/jobs`. `discovery.domain_index` covers the shapes a prefix query
   cannot reach: Workday, iCIMS, BambooHR, Eightfold, Personio and Breezy HR
   tenants on their own subdomains, and UKG Pro/Workable boards whose employer
   sits in the path.
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
   `crawl.started`, periodic `crawl.progress` (processed refs, total refs, new
   and updated postings) and `crawl.completed` / `crawl.failed`. New jobs land
   in the database and are immediately searchable through your search profiles,
   with their hard filters applied.
8. **Cold start.** A fresh install has no run state, so the worker first
   registers up to `discovery.bootstrap_seed_sources` (default 2,000) hiring
   seed boards — largest first, empty ones excluded — and then runs
   `discovery.warmup_passes` (default 5) passes back-to-back, each still bounded
   by the pass caps and the crawl gate. A pass keeps
   `discovery.seed_boards_per_pass` slots for the seed tail so live discovery
   cannot starve it. The loop stops early once a pass registers nothing new.
9. **The full seed arrives before the first pass.** On launch an import walks
   the bundled seed and registers every board that is not already known, in
   batches and without network calls (the whole catalogue takes seconds). Boards the
   build saw hiring are normal sources; boards it saw empty enter the probation
   path so they cannot displace hiring boards in a crawl. The sweep is
   resumable and idempotent, records the manifest total when it completes, and
   is skipped on later launches for the same seed.  Immediately after it, the
   worker drains every remaining never-attempted board in batches of 400 with
   no sleep between batches; the crawl gate (`crawl.global_concurrency`, default
   8) and the per-origin rate limit decide the actual request rate, so the
   first sweep runs at the gate's throughput instead of a fixed number of
   boards per interval.  A board is marked attempted when its crawl starts, so
   an interrupted drain resumes with the remaining boards on the next launch.
   The hourly cadence is unchanged for boards that already have a crawl.

Each enabled source is also kept on its own loop at its effective interval —
narrowed by its relevance tier — so boards and their postings are re-checked,
updated when they change, and marked removed after repeated full listings no
longer contain them. Both paths share the registry and a due check, so a board
that one just refreshed is not fetched again by the other. A restart of the app
does not reset any of it: the last run time, the rotation cursor and every
source schedule are stored in the app database.

The app-wide activity bar and the Discover status card read the same live
status: enabled boards, boards already crawled at least once, boards still
waiting, active crawls, total postings and the last crawl error. They poll
every two seconds while work is active and every twelve seconds when idle, and
`crawl.*` / `discovery.*` events refresh them immediately. This is why the
screen answers "is it crawling?" without running a pass or pressing Refresh.

## Sources, not seeds

`discovery.ats_directory` ships an offline seed list of 58,870 boards across
seventeen connectors: Greenhouse, Lever, Ashby, SmartRecruiters, Workable,
BambooHR, Workday, iCIMS, Oracle Recruiting Cloud, UKG Pro (UltiPro),
Paylocity, JazzHR, Taleo, Eightfold, Rippling, Personio and Breezy HR, each
confirmed live through the board's own public ATS API. It is stored as one NDJSON shard per
connector (`seeds/boards/*.ndjson`) plus `seeds/manifest.json`, which records
each shard's board/posting counts, its checksum and the connectors whose zero
is not trustworthy. A shard is verified against that checksum on read, over
its LF bytes: `.gitattributes` pins the shards to LF so a Windows checkout
cannot turn a healthy file into a checksum mismatch, and the reader normalises
CRLF anyway. The current manifest reports 58,870 boards and 31,892
hiring boards, generated 2026-11-26. A record carries the URL, the
postings observed at build time, the verification date and the tenant key;
hiring boards are written before empty ones. It exists so a brand new install
has something to crawl immediately (and so tests do not need the network). It
is **not** the discovery engine: the app never depends on it to find
employers, and it going stale only removes a head start, not discovery.
`scripts/build_seed.py --refresh` re-verifies and re-ranks it, and
`--dry-run --report` prints per-connector live/hiring/empty/dead counts without
writing.

## Hiring, empty and dead boards

An index hit is not proof of a live board: measured on settled candidates, 53%
no longer exist. Discovery probes a candidate before registering it and keeps
three outcomes apart. **hiring** boards register immediately. **dead** boards (a
404/410, or a redirect off the tenant host) are dropped and cached. **live but
empty** boards are kept — a quiet employer hires next month — but they enter a
persisted probation queue with its own small per-pass budget and a weekly
cadence, never a normal registration slot; a few are crawled per pass so the
tier cannot eat the crawl budget. A zero is only trusted where the listing is
authoritative (a JSON count or a server-rendered list); on iCIMS, Taleo and
Workday it means "not observed", so the board is treated as unknown and keeps
the previous behaviour. `probe_status` on the source records the verdict, and
a board that fails two crawls backs off to weekly. Counts (probed, live, empty,
dead, queued) are shown after a manual pass on the Discover screen.

Harvesting that list is done in the bulk web indexes, in 10,000-row slices.
Board discovery keeps one row per board — per tenant, except Workday, where a
tenant runs one board per career site; the funnel separately reads the
job-detail URLs in the same rows and verifies them individually. A single
Greenhouse slice that returned 1190 rows used to yield roughly 100 tenants and
now yields about 800. A slice that
comes back completely full is walked again as narrower buckets, and each
completed slice (and each UKG Pro chunk) is checkpointed, so an interrupted run
resumes where it stopped instead of starting over. Checkpoints are written to a
temp file and renamed into place, so a kill mid-write cannot truncate the last
one, and verification checkpoints every 1,000 settled boards as well as at the
end — a run interrupted after hours of verifying keeps every outcome it had
settled, and `--incremental` resumes from there: live boards settled in the
last seven days are written without being probed again, and dead boards inside
their re-check window are skipped. A later expansion pass runs
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
  Eightfold, Personio, Breezy HR, UKG Pro and Workable boards are enumerated by
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
| `max_crawl_sources_per_run` | `60` | Sources crawled per steady-state pass (oldest attempt first); the startup drain is bounded by the crawl gate instead |
| `cursor_step` | `12` | Index slices walked per pass (`discovery.domain_index` walks three of them, because domain queries are slower) |
| `max_index_slices_per_day` | `288` | Daily ceiling on index slices across every pass; `0` pauses index discovery while crawling continues |
| `web_index_limit` | `200` | Rows requested per Common Crawl query |
| `web_index_cdx_limit` | `10000` | Rows requested per CDX query (one whole bucket) |
| `bootstrap_seed_sources` | `2000` | Largest hiring seed boards registered before the first crawl; the background import covers the rest of the catalogue |
| `warmup_passes` | `5` | Passes run back-to-back on a cold start (`0`/`1` disables) |
| `seed_boards_per_pass` | `25` | Seed boards reserved per steady-state pass so live discovery cannot starve the tail |
| `funnel_enabled` | `true` | Harvest and verify posting URLs; disabling it restores plain due-based crawling |
| `funnel_postings_per_pass` | `50` | Posting URLs verified per pass (one fetch each) |
| `tier_intervals_s` | `[3600, 86400, 604800]` | Minimum crawl spacing for hot / warm / cold sources |

To stop a specific provider or source, disable it in Settings → Sources /
Plugins; nothing else changes.

Every completed crawl, source completion, pass completion and verified posting
also triggers the shared UI refresh. Home, Find jobs, Saved, Job detail,
Applications, Keywords and Profile matches re-fetch after a short debounce; the
Refresh buttons remain as a manual fallback. In a preview without an event
bridge, the UI falls back to a five-second poll while discovery is active.

## Source lifecycle, crawl budgets and the crawl event log

**Removing a source archives it.** `sources.delete` keeps the row: its jobs,
observations and crawl runs stay attached, and the source leaves every list and
schedule. A source that has been crawled can therefore be removed without losing
the history it produced (or, as it used to, failing on its own foreign keys). 
Re-adding the same URL creates a new, active source; the archived one remains for
its history.

**A crawl is bounded per source.** `crawl.max_refs_per_source` (default 500)
caps how many postings one run looks at, and `crawl.max_duration_s` (default
300) caps how long it may take. A source can narrow either with
`limits: {max_refs, max_duration_s}` in its schedule configuration. A truncated
run records `truncated: true` in its stats and **never** retires postings it did
not see, because a partial listing cannot prove a job is gone.

**Failures are recorded, not just counted.** `crawl_events` holds one row per
refusal, challenge, timeout or budget stop, with its stable code, message and
URL. `run.stats` still carries the counts (`errors`, `errors_by_code`,
`error_samples`); the event log is what makes a red source explain itself
without opening the rotating log files.
