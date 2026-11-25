# Job Signal

Job Signal is a **local-first job intelligence desktop application**. Everything
runs on your machine: a Tauri 2 shell, a React UI, and a Python worker sidecar
that owns the database and all business logic. There is no account, no cloud
dependency, and no telemetry.

The repository is named `JobSignal` for historical reasons; the product is named
**Job Signal** and the Python package is **lodestar**.

> **Current milestone: 7 — Applications and autofill (complete).**
> The desktop shell, IPC contract, plugin registry, Settings and Diagnostics ship
> from Milestone 0; sources, ATS connectors and crawls from Milestone 1;
> the universal extraction pipeline from Milestone 2; search profiles, hard
> filters, full-text search, fingerprints, identity resolution, repost events
> and the Seen/Duplicates view from Milestone 3.
> Milestone 4 makes the app find jobs on its own: discovery enumerates career
> sites from a public web index (no seed file, no user-added sources), and every
> enabled source is crawled on its own persisted schedule within per-pass
> budgets. See [`docs/how-discovery-works.md`](docs/how-discovery-works.md).
> Milestone 5 imports resumes, extracts sections, contact details, experience,
> education, certifications, projects, evidence-labelled skills and years of
> experience, scores jobs with `fit.weighted_v1`, and exposes the ranking,
> weights, skill-status and profile-editing UI.
> Milestone 6 ranks crawl budget by relevance: harvested posting URLs are
> verified with a single fetch through identity resolution, sources are tiered
> hot/warm/cold from the candidate profile and hard filters, and a profile
> change retiers immediately.

## What Milestone 6 adds

- **Index-harvested postings** (`discovery.funnel`): the same public-index
  queries that find boards also yield posting URLs. Up to
  `discovery.funnel_postings_per_pass` (50) are verified per pass with one
  connector fetch and the normal extraction pipeline, then deduped against the
  board listings by identity resolution. A verified posting is marked "via
  index" until a full listing confirms it.
- **Relevance tiers** (`app/application/tiers.py`, `app/domain/services/tiers.py`):
  the active resume's skills, the user's target titles and search-profile hard
  filters rank each source hot (every pass), warm (daily) or cold (weekly).
  The effective interval is the tighter of the source's own schedule and its
  tier, so a tier never crawls more often than its interval and disabling the
  funnel restores plain due-based crawling. Only real tier transitions are
  evented (`discovery.tier_changed`); clearing the profile removes stale tiers.
- **Immediate retiering**: `resume.import`, `resume.set_active`,
  `resume.update_overrides`, `resume.set_skill_status`,
  `search_profiles.save` and `search_profiles.delete` recompute tiers and
  resync the scheduler instead of waiting for the next discovery pass.
- **Budget, not lever**: the funnel never raises the registration/crawl caps or
  the global gate; index walks are also bounded per UTC day
  (`discovery.max_index_slices_per_day`).
- **Discover screen**: source rows show their tier and the reason for it; job
  rows show "via index" provenance.

## What Milestone 3 adds

- **Fingerprints and identity resolution** (`app/domain/services/fingerprints.py`,
  `app/domain/services/identity.py`, `app/application/dedup.py`): canonical URL
  and ATS keys, normalized title/location, exact description hash, 64-bit
  simhash and a 64-permutation MinHash. Ingestion resolves a posting it has
  never seen against the same company's recent jobs and writes
  `job_fingerprints` and `repost_events`.
- **Repost behaviour**: the same job at a new URL/ATS id becomes an alias of
  the existing job (`exact_duplicate`/`reposted_identical`); identical content
  with a later `posted_at` also records `posted_date_reset`; a change above the
  material threshold — including skills in the posting's requirements — becomes
  a new snapshot of the same job (`materially_updated`); a merely similar
  posting stays separate and gets a `similar` relation (`similar_distinct`).
  Evidence and confidence are stored on every event.
- **Manual merge/unmerge** (`jobs.merge` / `jobs.unmerge`): a merge records a
  `user_merged` relation and aliases the duplicate URL to the primary; unmerge
  removes both and records `user_unmerged`, which automation never reverses.
- **History and Seen views**: `jobs.get_detail` returns the job with snapshots,
  observations, relations and repost events; `/jobs/:id` renders it. `jobs.list_seen`
  feeds `/seen` with removed postings and duplicates, including the related ids
  needed to unmerge.

## What Milestone 5 adds

- **Resume import and versions** (`app/application/commands/resume.py`): PDF,
  DOCX and TXT resumes are copied into a content-addressed store and parsed
  into immutable resume versions on a candidate profile.
- **Sectioner and entity extraction** (`app/domain/services/sectioner.py`):
  sections (experience, education, skills, projects, certifications, summary,
  contact), work-experience entries split by date ranges with employer/title and
  bullets, education records (institution, degree, field, dates),
  certification/project entries, contact details (stored but never used in
  matching), and `years_experience_total` computed from merged, non-overlapping
  date ranges. Records are persisted to `experience_records`,
  `education_records` and `profile_items`.
- **Skill evidence** (`app/domain/services/keywords.py`): taxonomy v1 mentions
  are labelled `explicit_with_experience`, `explicit`, `inferred` or `limited`
  (spec §10.3), each with the character spans that back it and, where a role
  mentions it, merged `duration_years` and `recency_years`. Matching weights
  skills by that evidence strength.
- **Manual edits win.** Import merges into existing profile overrides instead
  of replacing them, so a manually corrected value is not overwritten by a
  later parse (spec §10.4). The Resume screen edits contact details, experience
  entries and titles, records per-skill statuses, and adds skills the parser
  missed; all of it is stored as overrides on top of immutable parser output.
- **Matching** (`app/application/commands/matching.py`, `fit.weighted_v1`):
  six dimensions (skills/resume, experience/seniority, responsibilities,
  location/workplace, compensation, freshness). Skills use evidence and user
  status; experience uses the seniority ladder, stated years and title overlap;
  responsibilities use resume/job term overlap; location and compensation use
  the saved job preferences; freshness uses posting-age decay. Unavailable
  dimensions are excluded from the aggregate rather than scored zero, and
  dimension coverage is reported with every result.
- **IPC and UI**: `resume.import`, `resume.parse`, `resume.list_versions`,
  `resume.get_profile`, `resume.set_active`, `resume.update_overrides`,
  `resume.set_skill_status`, `keywords.extract`, `matching.run`,
  `matching.get_results` and `matching.update_weights`, plus a Resume screen
  for importing, listing versions, editing skill statuses and weights, and
  reading fit dimensions and explanations.
- **Weights and explanations**: the active weight set is stored in
  `scoring_configurations` and used by the next run; per-dimension scores and
  reason-level explanations are persisted and returned with results. An input
  hash over the model, weights, resume version, overrides, skill statuses and
  job snapshot makes unchanged results reusable and stale ones recompute.
  Location/workplace, compensation, freshness, responsibilities and experience
  now use real inputs instead of fixed baselines.

See [`docs/how-resume-and-matching-works.md`](docs/how-resume-and-matching-works.md).

## What Milestone 4 adds

- **Seed-free discovery** (`app/plugins/discovery/web_index.py`):
  `discovery.web_index` enumerates ATS tenant boards from public web URL
  indexes — Common Crawl's index server primary, the Internet Archive CDX API
  as fallback — with no API key, no bundled list, no user input. Each pass
  walks `discovery.cursor_step` (default 12) `(ATS host, leading-character
  bucket)` slices in parallel, so coverage grows with every pass (discovery
  runs continuously, one bounded pass per interval) and wraps around to
  refresh. Each query asks for a whole block of rows (the CDX
  fallback requests 10,000 — measured at ~13 s for a complete bucket), not a
  small sample. A pass whose slices are all unreachable is retried next pass
  (the rotation cursor is held). Design and trade-offs:
  [ADR 0005](docs/adr/0005-m4-automatic-discovery.md).
- **Subdomain and path ATS discovery** (`app/plugins/discovery/domain_index.py`):
  `discovery.domain_index` walks Workday, iCIMS, BambooHR and Eightfold tenants
  with domain index queries and UKG Pro/Workable boards with prefix queries,
  rebuilding each board URL from the captured URL — including Workday's
  `{locale}/{site}`, which the hostname alone does not carry. Domain scans are
  slow (40–90 s each), so a pass walks three rotating slices; a slice the
  archive throttles is logged and re-walked when the rotation wraps. A new
  tenant no longer waits for a seed refresh.
- **UKG Pro (UltiPro) boards** (`app/plugins/ats/ultipro.py`): a board is
  addressed as `recruiting.ultipro.com/{employer}/JobBoard/{id}`. The connector
  reads the board's own unauthenticated `LoadSearchResults` listing (50-row
  `Top`/`Skip` pages) and the posting JSON embedded in its detail page, and
  `scripts/build_seed.py` verifies and harvests those boards like the
  subdomain-based ATSs.
- **Wrapped career sites and Big Tech** (`app/plugins/ats/`):
  `ats.eightfold` reads the public Eightfold `apply/v2/jobs` listing and detail
  APIs, and `ats.phenom` is a configurable `/widgets` adapter (endpoint
  template plus field mapping) for Phenom tenants; both are validated live.
  `ats.amazon` uses Amazon Jobs' public `search.json`; thin `ats.taleo`,
  `ats.jazzhr` and `ats.paylocity` connectors handle server-rendered Taleo
  career sections, JazzHR boards on `{tenant}.applytojob.com`, and Paylocity
  boards addressed by company id. Marketing wrappers (Beamery,
  Radancy, Clinch, SmashFly and custom Phenom hosts) are not scraped
  individually: `ats.career_site` detects the embedded Workday/iCIMS/ORC/
  Taleo/Eightfold/Phenom URL in an iframe or apply link and delegates to that
  connector. With no embedded ATS it harvests public job links from the page
  and sitemap, then sends each detail page through the extraction pipeline.
  Google and Meta have no connector against internal endpoints; their public
  pages go through that same link/JSON-LD path, and stay manual-URL sources
  when no public postings are exposed.
- **Workable, BambooHR and Oracle Recruiting Cloud** (`app/plugins/ats/`):
  `ats.workable` reads `apply.workable.com/api/v1/widget/accounts/{account}`
  (with `details=true`, so one response carries descriptions); `ats.bamboohr`
  reads `{company}.bamboohr.com/careers/list` plus one detail call per posting;
  `ats.oracle_recruiting` pages Oracle's Candidate Experience
  `recruitingCEJobRequisitions` finder and fetches `recruitingCEJobRequisitionDetails`.
  Workable and BambooHR boards are enumerated by `discovery.domain_index`;
  Oracle boards by `discovery.oracle_recruiting`, which rotates Oracle HCM
  pods (a pod-scoped index query answers in 17–38 s) and rebuilds each
  `{host}/hcmUI/CandidateExperience/{lang}/sites/{site}` board from the URL.
- **The seed file is a warm start, not the engine.**
  `discovery.ats_directory` ships 35,572 boards across eleven ATSs (Greenhouse,
  Lever, Ashby, SmartRecruiters, Workable, BambooHR, Workday, iCIMS, Oracle
  Recruiting Cloud, UKG Pro/UltiPro and Paylocity), each
  confirmed live through its own public ATS API and ordered largest-first by
  open postings (refreshed via `scripts/build_seed.py`), so a cold install has
  something to crawl within minutes while the web-index rotation fills in
  long-tail companies. The seed covers those eleven ATSs only; Eightfold,
  JazzHR and the remaining vendors are found by live discovery.
  Deleting the seed does not stop discovery.
- **`discovery.auto_discover`** (`app/application/auto_discover.py`): asks
  every enabled provider for candidates, dedupes by canonical URL, registers
  up to `discovery.max_new_sources_per_run` new sources (connector from the
  provider's ATS hint or the URL classifier), then crawls the sources that are
  due — stalest attempt first, in parallel, up to
  `discovery.max_crawl_sources_per_run`. On a cold start (no run state yet)
  it runs `discovery.warmup_passes` (default 5) passes back-to-back so the
  bundled seed list is registered in minutes rather than over the first
  days. Per-provider and per-source failures are isolated and counted,
  never fatal.
- **Bounded work.** A source is crawled by the scheduler and by the pass only
  through one shared crawl gate (`crawl.global_concurrency`); the pass has a
  registration cap and a crawl cap, so a large source list never turns a pass
  into an hours-long run. Nothing crawls on app launch.
- **Cadence and coverage survive restarts.** The last run time *and* the
  rotation cursor are stored in `app_settings` (`auto_discover_state`). The
  loop waits `AUTO_DISCOVER_STARTUP_DELAY_S` after launch, then checks every
  minute whether a run is due (`discovery.auto_discover_interval_minutes`,
  default 60; the legacy `auto_discover_interval_hours` key still works when
  minutes is unset).
- **Crawl events.** Every triggered crawl records a `crawl_runs` row and
  publishes `crawl.completed` / `crawl.failed`, so new jobs flow straight into
  search profiles (hard filters apply to searches as in M3).
- **Discover screen**: an "Auto-discovery" panel that says the pass runs by
  itself, mentions the coverage limit, and offers a manual run that reports
  sites found, new sources, crawled and failed counts.

## What Milestone 2 includes

- A **universal extraction pipeline**: strategies run in order (per-source
  override supported) and merge their results field by field, keeping the most
  confident value. Every field carries its strategy as provenance.
- Six **extraction strategies**: `public_endpoint`, `jsonld`, `embedded_json`,
  `static_html`, `generic`, `partial`, plus `rendered_dom` when a browser is
  available.
- **Rendered-DOM extraction** via an optional Playwright dependency
  (`pip install "lodestar[render]"` then `playwright install chromium`). A page
  that disallows crawling, or that is a challenge/CAPTCHA page, is not
  extracted at all.
- **Three more ATS connectors**: SmartRecruiters, Workday and iCIMS, all
  behind the `JobSourceConnector` port and contract-tested.
- **HTML parsing uses the standard library** (`html.parser`), so no new
  required dependency.
- **Provenance** surfaces in the data model and the Discover screen.


## What Milestone 7 adds

- **Application tracking** (`applications.*`): a job snapshot, a free-form
  status history with an append-only event log, outcomes, and per-company
  history. Pre-application states (`interested`, `saved`) never start a
  cooldown.
- **Company-aware suppression**: an applied job hides its own reposts, similar
  roles at the same company for 180 days, and — only when enabled — every role
  at the company for 90 days. Each decision stores its evidence, explanation,
  expiry and the policy that produced it. Hidden roles leave the default
  result lists and appear in Previously Seen with a Restore action; restoring
  is sticky.
- **Suppression policy UI**: cooldown lengths, similarity threshold, exact-repost
  and company-wide toggles, applied immediately with a re-evaluation of hidden
  roles.
- **Reviewed autofill** (`browser.*`): a headed browser you control, a form
  model and classifier, a review panel that lists what will be filled and what
  is blocked, and a filler restricted to fill/select/check/upload primitives.
  Nothing can submit: there is no click, press or evaluate capability, step
  navigation is disabled, and submission requests are aborted at the network
  layer for the whole life of the window. You submit by clicking
  **I'll submit in the browser**, which is the only thing that turns the guard
  off (`browser.handoff`); another fill turns it back on. A CI-blocking safety
  suite proves it against fixture forms, and a real Playwright run against a
  local fixture server counts zero submissions — including a POST delayed well
  past the fill — while confirming a human Submit click still works.
- **Autofill answers**: Settings → Autofill answers is the answer book — the
  only values the filler may type. Re-checking a form re-inspects the current
  step, saving applies without a restart, and one step is bounded in writes.

See
[`docs/how-applications-and-autofill-work.md`](docs/how-applications-and-autofill-work.md)
and [ADR 0008](docs/adr/0008-session-long-submission-interceptor.md) for why the
submission guard lives for the whole session.

## Hardening after Milestone 7

- **Removing a source archives it.** The row stays, and so do its jobs,
  observations and crawl runs; it leaves every list and schedule
  (`sources.delete`). Deleting the row used to fail on its own foreign keys.
- **Cooldowns end on their own.** The worker lapses due suppressions on a timer,
  and the default lists ignore a suppression whose `expires_at` has passed, so a
  hidden role reappears even if nothing else triggers a sweep.
- **Per-source crawl budgets.** `crawl.max_refs_per_source` (500) and
  `crawl.max_duration_s` (300), overridable per source with
  `limits: {max_refs, max_duration_s}`. A truncated listing never retires
  postings it did not see.
- **A crawl event log.** Refusals, blocks and budget stops are written to
  `crawl_events`, so a failing source is diagnosable from the database and not
  only from a rotating log file.
- **Per-method IPC timeouts.** A crawl, a discovery pass or a matching run is
  not bounded by one HTTP request, so the shell waits for the method's declared
  budget instead of a fixed 60 s, which used to report a failure for work that
  was still running.

## What Milestone 8 adds (first slice)

Posting intelligence is now stored, not only computed. Every evaluated job gets
an append-only assessment built from eight explainable signals, each carrying
its evidence and observation time, plus the exact model configuration that
produced it.

- **History that cannot be rewritten.** `signal_observations`,
  `risk_assessments`, `risk_model_versions`, `classification_changes`,
  `company_hiring_summaries` and `application_endpoint_observations` are
  append-only; database triggers abort an `UPDATE` or `DELETE`.
- **Separate risk and confidence.** A weak signal can never lift a posting above
  "needs verification"; a blocked page or a login wall is missing evidence, not
  a negative one.
- **Reproducible verdicts.** An assessment references the exact signal rows, the
  model version and a config hash, so re-running the pure model reproduces its
  numbers. A test asserts this.
- **No duplicate assessments.** Identical inputs reuse the existing row; a new
  snapshot, repost, endpoint check, configuration change or daily pass appends a
  new one and records any class change.
- **Company hiring summaries** are written at most once per company per day and
  only carry labels at the *adequate* sample tier; below it they show raw counts.
- **`posting_intelligence.*` IPC** (`evaluate`, `get_signals`,
  `get_risk_history`, `get_company_assessment`, `configure_model`). The M0 guard
  that blocked the prefix is retired; the allowlist now explicitly forbids
  `browser.submit`, `browser.click`, `browser.press`, `browser.evaluate`,
  `browser.type` and `applications.submit`.

- **Polite endpoint verification.** `jobs.verify_now` (and a budgeted sweep in
  the maintenance loop) fetches the employer's application page over plain HTTP
  through the policy client: GET only, robots rules, redirect re-validation and a
  7-day per-job budget. It records the state it observed and never clicks,
  types or submits. A timeout, a block or a login wall is recorded as such and
  treated as missing evidence, never as a negative signal.
- **Risk on every job surface.** The Discover list and the job detail view show a
  risk chip that names the class and reports confidence separately (never a bare
  percentage), with the evidence and any classification history behind it. The
  `/posting-intelligence` screen explains the model, lets you save a new model
  version, run a bounded evaluation and look up company evidence.

See [docs/how-posting-intelligence-works.md](docs/how-posting-intelligence-works.md)
and [ADR-0011](docs/adr/0011-posting-intelligence-history.md).

## Finding your best jobs (M8.5)

"Best" is three separable, explainable inputs, and it is never a probability of
an interview:

1. **Satisfaction** — `fit.weighted_v1` over six dimensions with your weights,
   applied after the profile's hard filters.
2. **Success evidence** — credibility `1 − risk`, kept separate from confidence,
   plus company hiring evidence.
3. **Actionability** — freshness, endpoint state, suppression and salary data.

`jobs.ranked` joins the latest stored fit and the latest assessment, applies the
profile's credibility policy and returns a `rank_score` plus the reasons behind
it:

```
rank_score = fit × (1 − 0.5 · risk · min(1, confidence / 0.5))
```

`deprioritize` is the default and never hides a row. `show_all` orders by fit
only. `hide_high_risk` hides only when risk is high *and* confidence is at least
0.5. `require_evidence` shows only postings assessed as likely active. Hidden
rows stay reachable with the "Show hidden" toggle, and every row can explain its
order. Ranking runs over a bounded window of the most recent matches (500 by
default, reported in the response) so a large database stays responsive; the
window is stated in the UI.

The Discover screen is now the ranked shortlist with policy and sort controls,
fill-in-your-profiles filters, per-row explanations and save toggles. It pages
25 or 50 rows at a time, keeps a true filtered total, and offers quick presets
for new matches, strong fit, needs-verification and remote roles. **Focus
review** walks the current queue one job at a time with save, dismiss, open and
skip actions (`s`, `d`, `o`, `j`/`→`, `k`/`←`). Saved jobs have their own screen,
and search profiles store both hard filters and a credibility policy.

See [docs/how-ranking-works.md](docs/how-ranking-works.md).

## Daily cockpit and volume-safe lists

Today adapts to the account. A new install still gets the full six-stage
search plan; once a resume, a source and either a saved/applied role exist, it
becomes a daily focus view: new matches, the ready-to-apply queue and roles
waiting 14+ days, plus this week's applied/response/interview/offer counts. The
ranked top three stay visible, and the sidebar badges reuse the same summary
instead of loading the full application list again.

The list contracts are honest at volume. `applications.list` takes
`view`, `query`, `sort`, `limit` and `offset`, returns only the requested page,
reports the filtered `total`, and includes a whole-history `summary`
(`total`, `applied`, `active`, `interviewing`, `offers`, `closed`, `follow_up`,
`applied_last_7_days`, `responded_last_7_days`). `follow_up` means applied,
acknowledged or recruiter-contacted roles with an `applied_at` at least 14 days
old; the UI calls that "waiting 14+ days", never "no response". Each row also
carries the resolved `company_name`, joined in one batch. `jobs.list_saved`
takes `view` (`ready` hides already-tracked roles), `query`, `sort`, `limit` and
`offset`, and returns the filtered total. `jobs.ranked` accepts `min_fit` and
`risk_classes` for display presets, and both stores plus the SQL filter builder
support `first_seen_after` for "New since last visit". The previous visit
timestamp stays in place while the session is open, so Today's link and Find
jobs' preset use the same cutoff; a first run falls back to seven days, and the
new baseline is recorded when the app is backgrounded or closed.

Saved is an apply queue: strongest fit first, with mark-as-applied advancing to
the next ready role and updating the shared counts. Applications is a workspace
with a summary band, counted tabs (including Waiting 14+ days), URL-backed
search/sort/paging, quick outcomes and expandable timelines; response analytics
and hidden roles live in their own tabs. Leaving setup incomplete is still the
first workflow priority, followed by the ready queue, new matches, follow-ups
and application review.

No ranking or scoring formula changed. The cockpit only exposes existing
evidence and counts.

## Outcomes, analytics and calibration (M9)

Each tracked application accepts a confirmed outcome (acknowledged, recruiter
contacted, interview, rejection, offer, or *no response*). `no_response` is a
record, never a posting-risk signal.

`applications.get_analytics` groups your response history by risk class, endpoint
state, repost count, company tier, fit band and posting age. Every group reports
`n`, a Wilson 95% interval and an `insufficient` flag below 5 applications; the
UI hides the rate for those groups. These are associations in your own history,
and they never reorder jobs automatically.

`posting_intelligence.get_calibration` compares predicted risk with endpoint
observations and returns a proposal only at 30+ observations. Accepting a
proposal means saving a new model version, so earlier assessments keep their
history.

See [docs/how-outcomes-and-calibration-work.md](docs/how-outcomes-and-calibration-work.md).

## Companies and merging (M9 breadth)

`companies.list` and `companies.get_detail` power the Companies screen: postings,
active counts, reposts, your own applications and the daily hiring summary at its
sample tier. Small samples show counts, never labels.

Two records for the same employer can be merged (`companies.merge`) and separated
again (`companies.unmerge`). A merge is an append-only decision in
`company_merges`: nothing is updated or deleted, and the canonical survivor is
resolved when evidence is read. Lists, detail, job rows and summaries all read
under the survivor; `companies.get_merge_history` returns the audit trail, which
the screen shows with an explicit confirm step before each merge.

See [docs/how-companies-work.md](docs/how-companies-work.md) and
[ADR-0013](docs/adr/0013-company-merge-and-keyword-history.md).

## Keyword demand (M9 breadth)

Keywords are extracted once per posting revision and stored with it
(`job_keyword_revisions`, `job_keywords`), filled by the normal assessment pass.
`keywords.prevalence` reports the share of postings mentioning each skill, and
`keywords.coverage` compares those skills with your profile. Both disclose `n`,
hide the rate below 10 postings, and treat unreadable postings as missing evidence
rather than zero demand. Reads are batched, and company scope resolves merges. The
Keyword Insights screen carries a "How this works" explanation.

See [docs/how-keywords-work.md](docs/how-keywords-work.md).

## Trends and weight suggestions (M10)

`keywords.trends` reads the same stored keyword history as a per-month time
series: postings in scope, mention counts per keyword, and a share only when
the period holds at least 10 postings. Empty periods are reported as missing
evidence, and company scope resolves merges. Two batched reads feed a whole
response.

`matching.get_weight_proposals` compares the stored fit dimensions of
applications that got a reply with those that did not. At 20+ applications
with at least 5 per side it returns an explainable suggestion (gap, sample,
changed dimensions); otherwise it says `insufficient_evidence` and still shows
the observed associations. Nothing is applied automatically:
`matching.apply_weight_proposal` needs an explicit confirmation and appends a
row to `fit_weight_versions` (migration 0017), which is append-only, so the
previous weighting is always recoverable via `matching.get_weight_history`.
Manual weight edits are recorded the same way.

Risk badges now open their own explanation instead of relying on a hover
tooltip, and the Posting Intelligence screen has a Weight suggestions tab.

See
[docs/how-keyword-trends-and-weight-suggestions.md](docs/how-keyword-trends-and-weight-suggestions.md)
and [ADR-0014](docs/adr/0014-associations-and-weight-history.md).

## Hardening (M11)

- **Benchmarks.** `python scripts/bench_hot_paths.py` seeds a deterministic
  SQLite database (20 companies, 500 postings, 100 applications, one keyword
  revision per posting) and measures every hot read path the plan names:
  `jobs.list`, `jobs.ranked`, `applications.get_analytics`,
  `keywords.prevalence` and `keywords.trends` — plus the ranking surfaces'
  search/detail reads `jobs.search`, `jobs.get_detail` and
  `companies.get_detail`. `NAMED_HOT_PATHS` is the plan's list and a test pins
  it, so a scenario cannot be dropped silently. Absolute budgets live in
  `BUDGETS_MS`; the checked-in
  baseline (`benchmarks/hot_paths_baseline.json`) is compared with a 1.25x
  threshold plus 5 ms of scheduler slack (each scenario is warmed up first; the
  small floor only absorbs timer noise, so a real slowdown still fails), and
  `--write` refreshes it deliberately.
- **Backup and restore.** `system.export_backup`, `system.import_backup` and
  `system.get_backup_history` back the Settings screen. Import requires an
  explicit confirmation, only fills an empty database (no overwrite mode), and
  verifies every table's row count before it reports success. Both directions
  are recorded in the append-only `backup_runs` table (migration 0018).
  See [docs/backup-and-restore.md](docs/backup-and-restore.md) and
  [ADR-0015](docs/adr/0015-local-backup-and-restore.md).
- **Conditional endpoint reads.** Endpoint checks send `If-None-Match` from the
  stored `ETag`, fall back to `If-Modified-Since`, and on a `304` record a
  revalidation that reuses the previous classification instead of refetching.
  See [ADR-0016](docs/adr/0016-conditional-endpoint-requests.md).
- **Auto-update stays off.** [ADR-0012](docs/adr/0012-auto-update.md) remains the
  decision (disabled, no updater plugin, manual download from a signed
  manifest); `tests/safety/test_auto_update.py` now enforces that the Tauri
  config carries no updater and that the ADR keeps its rollback rule.

All checks run against these: full pytest with the coverage gate, ruff, mypy,
import-linter, `gen_schemas.py --check`, `gen-ts-types.mjs --check`, frontend
`tsc`/`vitest`/`vite build`, and Rust `cargo test --lib`/`clippy`.

## Still not implemented

These are **intentionally absent** so far, and the IPC allowlist rejects them
(there is a test for it):

- Automatic update delivery: the build is installed manually from
  `frontend/src-tauri/tauri.conf.json` targets; nothing replaces itself
  (ADR-0012)

No browser automation is installed by default; Playwright is an opt-in extra.

### Verification limits in the current tree

These are not product gaps, but they are not claimed as verified by the current
headless pass either. See [`docs/ipc-parity.md`](docs/ipc-parity.md) for the
method-by-method audit and the exact commands.

- The packaged Tauri desktop shell was not launched. Rust allowlist and
  supervisor tests ran, and the Python worker was run over its real NDJSON
  protocol, but the native WebView handshake was not driven.
- Headed Playwright autofill was not exercised; Playwright remains an optional
  extra and the existing safety suite covers the fill-only boundary.
- No live external job board was crawled in this pass. Database-backed source
  list, crawl history and the workflow were exercised against a seeded SQLite
  database; live collection still needs a real board and network access.
- The browser preview remains sample-only: it does not implement `browser.*` or
  a real backup restore, and it is not evidence that the desktop worker path
  works. The desktop app always uses the real Tauri bridge.

## Repository layout

```text
app/
  domain/              # models, enums, policies, ports (Protocols), services
  application/         # commands, DTOs, orchestration, configuration, container
  infrastructure/      # SQLite, in-memory twins, HTTP, logging, clock, ids
  plugins/             # registry, isolation shim, builtin + ATS connectors
  worker/              # composition root, IPC dispatcher/transport, entrypoint
frontend/
  src/                 # React 19 + TypeScript routes, stores, services, copy
  src-tauri/           # Tauri 2 shell + Python supervisor + generated allowlist
schemas/ipc/           # generated JSON Schemas; methods.json is the allowlist
scripts/               # gen_schemas.py, gen-ts-types.mjs, dev.ps1
tests/
  unit/ contract/ integration/ safety/ swapability/ support/
docs/adr/              # architecture decision records
.github/workflows/     # CI matrix
```

Where the discovery code lives:

```text
app/domain/services/     # classifier.py, normalizer.py  (pure domain logic)
app/application/         # sources.py (registry + crawl runner), auto_discover.py,
                         # orchestration/scheduler.py, commands/
app/plugins/ats/         # greenhouse/, ashby/, lever/, smartrecruiters, workday,
                         # icims, eightfold, phenom, amazon, taleo, career_site
app/plugins/discovery/   # web_index.py (seed-free enumeration), ats_directory.py (bootstrap)
app/infrastructure/      # persistence (sources, jobs, snapshots, crawl runs)
```

Documentation for M4 lives in
[`docs/how-discovery-works.md`](docs/how-discovery-works.md) (user-facing: what
runs, what leaves the machine, coverage limits) and
[`docs/adr/0005-m4-automatic-discovery.md`](docs/adr/0005-m4-automatic-discovery.md)
(why discovery is seed-free, budgets, and the options that were rejected).

### Dependency rule

```text
domain (stdlib only)
   ▲
application (domain only)
   ▲
infrastructure / plugins (domain; bundles application DTOs at the ends)
   ▲
worker (composition root; may import everything)
```

`import-linter` enforces it in CI:

- domain must not import application, infrastructure, plugins, or worker
- application must not import infrastructure, plugins, or worker
- infrastructure must not import worker
- plugins must not import worker or application

Rust contains **no business logic** and never opens SQLite. It only spawns,
supervises, sizes, and forwards envelopes.

## Prerequisites

- Python **3.12+** (3.14 is used locally)
- Node.js **20+** (22 LTS in CI) and npm
- Rust stable (`rustup`) — after installing, add `%USERPROFILE%\.cargo\bin` to
  your `PATH` (restart the terminal) so `cargo` is discoverable by npm/Tauri.
- Tauri 2 system dependencies (Linux: `libwebkit2gtk-4.1-dev`,
  `libappindicator3-dev`, `librsvg2-dev`, `patchelf`; macOS: Xcode command line
  tools; Windows: WebView2, preinstalled on current Windows)
- No external SQLite install is required (`aiosqlite` bundles nothing extra and
  uses the platform SQLite).

## Setup and common commands

```bash
# Python
python -m pip install -e ".[dev]"

# Frontend
cd frontend
npm ci
```

### Run the worker directly

The worker requires an explicit data directory and speaks NDJSON on
stdin/stdout. stderr is structured JSON logs.

```bash
python -m app.worker.main --data-dir ./.lodestar-data
```

The database is `<data-dir>/lodestar.sqlite`; rotating logs live in
`<data-dir>/logs`.

### Run the desktop app

```powershell
# Windows: make sure the Rust toolchain is on PATH first
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
cd frontend
npm run tauri dev
```

Or use the helper script:

```powershell
.\scripts\dev.ps1
```

The Tauri supervisor spawns `python -m app.worker.main --data-dir
<app_data_dir>/worker`. In a dev build it also adds the repository root to
`PYTHONPATH` and runs the worker from there, so no `pip install` is required to
try the desktop app. Override with environment variables when needed:

- `LODESTAR_PYTHON` — Python executable (default: `python`)
- `LODESTAR_DATA_DIR` — explicit worker data directory
- `LODESTAR_REPO_ROOT` — Python source tree to run from (defaults to the repo
  root baked into the dev build)

A crashed worker is restarted with exponential backoff, capped at **5 restarts
in 5 minutes**.

### Dev database and schema checksums

The migrator enforces a per-migration checksum (SHA256 of the `.sql` file)
stored in `schema_migrations`. If a migration file changes between runs — for
example after pulling a new milestone — the stored checksum no longer matches
and the worker refuses to start, crash-looping until the restart budget is
exhausted.

The dev script `.\scripts\dev.ps1` handles this automatically: it removes the
ephemeral dev database at `%APPDATA%\com.jobsignal.app\worker\lodestar.sqlite`
before launching Tauri, so the worker applies the current schema and emits
`worker.ready`. This only touches local dev data; never production data.

To reset the database manually:

```powershell
Remove-Item "$env:APPDATA\com.jobsignal.app\worker\lodestar.sqlite*" -ErrorAction SilentlyContinue
```


### Run the UI in a browser (no Tauri)

```bash
cd frontend
npm run dev
```

A clearly labelled **Preview mode** banner appears and an explicit
`PreviewBridge` returns sample data for the full UI, including jobs, saved and
tracked applications, and company history. It never fetches the network. The
desktop app always uses the real Tauri bridge.

## Tests, lint, and generation

```bash
# Python: unit + contract + integration + safety + swapability (S1-S9)
python -m pytest -q

# Python with coverage and the per-layer gates (spec section 19.1)
python -m pytest -q --cov=app --cov-report=term-missing:skip-covered
python scripts/check_coverage.py

# Python lint and architecture
python -m ruff check app tests
python -m mypy
python -c "from importlinter.cli import lint_imports_command; lint_imports_command()"

# Frontend
cd frontend
npm run typecheck
npm test
npm run build

# Rust supervisor (allowlist negative test + backoff)
cd frontend/src-tauri
cargo test --lib

# Regenerate schemas and TypeScript types
python scripts/gen_schemas.py
node scripts/gen-ts-types.mjs
```

`python scripts/gen_schemas.py --check` and
`node scripts/gen-ts-types.mjs --check` fail when committed generated files are
stale. CI runs both and then `git diff --exit-code`.

`scripts/check_coverage.py` enforces the section 19.1 coverage gates on the data
`pytest-cov` just wrote: **domain ≥ 90%**, **application ≥ 85%**, **adapters
(`app/infrastructure` + `app/plugins`) ≥ 75%** line coverage. Each layer is
checked separately, so a large, lightly-tested adapter package cannot hide a
regression in the domain. A layer with no measured files fails rather than
passing trivially. CI runs the gate in the same job as the suite.

Integration tests spawn the real worker subprocess and cover the handshake,
hello/health, invalid params, forbidden methods, settings validation, plugin
listing, task progress/completion/persistence, cancellation, and graceful
shutdown. The worker uses the repository's `sys.executable`, so no extra setup
is needed.

`tests/safety/` guards the milestone scope: no crawling, browser, resume, or
machine-learning dependency may appear in `pyproject.toml` or in production
imports, migration `0001` may only create the M0 infrastructure tables, and no
forbidden IPC method may be registered.

## IPC protocol v1 (summary)

Transport: **NDJSON** over the worker's stdin/stdout, one JSON object per line,
maximum **8 MB** per line. stderr is for structured logs only.

Every envelope has `v: 1` and a `type` of `request`, `response`, or `event`.

On startup the worker emits:

```json
{
  "v": 1,
  "type": "event",
  "topic": "worker.ready",
  "seq": 0,
  "ts": "2026-01-01T00:00:00Z",
  "data": {
    "protocol_versions": [1],
    "app_version": "0.0.0",
    "schema_version": 1
  }
}
```

The UI then performs `system.hello` to negotiate protocol v1. Requests carry an
`id`; responses echo it with `ok` plus `result` or `error {code, message,
retryable, details}`.

### M0 methods

| Method | Purpose |
|---|---|
| `system.hello` | negotiate protocol v1 and return worker capabilities |
| `system.health` | status, DB schema version, data dir/disk usage, plugin health, queue depth |
| `system.shutdown` | graceful worker stop |
| `tasks.list` | list persisted `background_tasks` |
| `tasks.cancel` | cooperative cancel (no-op success for unknown tasks) |
| `settings.get` | merged `AppConfig` plus stored overrides |
| `settings.update` | validate and persist a settings patch |
| `plugins.list` | registry entries, state, capabilities, health |
| `plugins.enable` / `plugins.disable` | toggle and persist plugin state |
| `plugins.configure` | validate/persist plugin config |
| `plugins.get_capabilities` | capability map (optionally for one plugin) |
| `plugins.reset_circuit` | reset a plugin's circuit breaker |
| `diagnostics.fetch_url` | one sandboxed HTTP request (not a crawler) |
| `diagnostics.sleep` | no-op long-running task used to test progress/cancel/events |
| `sources.list` | configured job sources with their connector and interval |
| `sources.add` | classify a URL, store it as a source |
| `sources.set_enabled` | pause/resume a source |
| `sources.set_schedule` | change the crawl interval (0 disables scheduling) |
| `sources.delete` | remove a source |
| `jobs.list` | persisted jobs, optionally filtered by source or company |
| `jobs.search` | full-text search over jobs (FTS5) |
| `jobs.list_seen` | removed postings and duplicates, with reasons and related ids |
| `jobs.get_detail` | job plus snapshots, observations, relations and repost events |
| `jobs.merge` / `jobs.unmerge` | manual duplicate override (persisted, never auto-reversed) |
| `crawl_runs.list` | crawl history with per-run stats |
| `discovery.run` | crawl one source now, returning its stats |

The authoritative catalogue is
`app/worker/ipc/method_catalog.py`. It generates:

- `schemas/ipc/**/*.json` (via `scripts/gen_schemas.py`)
- `schemas/ipc/methods.json` (the method allowlist)
- `frontend/src/types/generated.ts` (via `scripts/gen-ts-types.mjs`)
- the Rust allowlist (via `frontend/src-tauri/build.rs`)

Rust exposes two commands, `worker_request({ method, params })` and
`worker_shutdown`, and forwards worker events on `worker://event`. Rust never
interprets payloads; it enforces the generated allowlist and envelope size.

Long-running work emits `task.queued`, `task.progress`, `task.completed`,
`task.cancelled`, and `task.failed` events with an optional `task_id`.
Crawls emit `crawl.started`, `crawl.completed`, and `crawl.failed`.

## Settings model

Defaults come from `AppConfig` (spec §20). User changes are validated as a
strict patch and stored under the `app_config` key in `app_settings`; reads merge
the patch over code defaults.

`paths.data_dir`, `paths.db_name`, `app_version`, and `protocol_version` are
**immutable for a run** and are rejected with `permission.denied`. Unknown keys
are rejected with `validation.invalid_params`.

## Adding a builtin plugin

Adding a plugin does not require touching application or UI code:

1. Create `app/plugins/<kind>/<name>/__init__.py` exposing a `PLUGIN` manifest
   and a `register(registry, deps)` function, or a module-level `register`.
2. Register a `PluginManifest` with a unique `id`, `kind`, `version`,
   `api_version="1"`, capabilities, and optional `network_hosts`.
3. Add one `(module_path, "register")` entry to `BUILTIN_PLUGINS` in
   `app/plugins/_builtin.py`.

Plugins are wrapped by `IsolationShim`: calls get a timeout, exceptions become
`PortError`s, and a circuit breaker opens after 5 consecutive failures or when
≥50% of the last 20 calls fail. Health is persisted to `plugin_registry`.

M0 ships exactly one builtin registration smoke plugin, `extract.demo`, which
performs no extraction. Test-only toy plugins (ATS connector, signal provider,
constant scoring model, extraction strategy) live in `tests/support/`.

## Architecture decisions

- [`docs/adr/0001-m0-foundation.md`](docs/adr/0001-m0-foundation.md) — M0 scope
  decisions, the deliberately narrow `0001` migration, the diagnostics
  `FetchUrl` swapability subject, and the browser preview bridge.
- [`docs/adr/0002-m1-discovery.md`](docs/adr/0002-m1-discovery.md) — the M1
  discovery framework: source registry, normalizer v1, incremental crawls,
  append-only snapshots/aliases, stable company identity, and the scheduler.
- [`docs/adr/0003-pdf-licensing.md`](docs/adr/0003-pdf-licensing.md) — PyMuPDF
  is AGPL-or-commercial: the distribution gate, the plugin boundary, and the
  permissive-parser alternative.
- [`docs/adr/0004-m2-extraction.md`](docs/adr/0004-m2-extraction.md) — the M2
  extraction pipeline, field-level merge, rendered-DOM via an optional
  Playwright dependency, and the isolation shim for connectors.
- [`docs/adr/0005-m4-automatic-discovery.md`](docs/adr/0005-m4-automatic-discovery.md)
  and [`docs/adr/0006-relevance-ranked-discovery-funnel.md`](docs/adr/0006-relevance-ranked-discovery-funnel.md)
  — seed-free discovery and the relevance-ranked funnel.
- [`docs/adr/0007-interval-scheduling.md`](docs/adr/0007-interval-scheduling.md)
  — persisted interval schedules (and why cron expressions are out of scope).
- [`docs/adr/0008-session-long-submission-interceptor.md`](docs/adr/0008-session-long-submission-interceptor.md)
  — why the submission interceptor lives for the whole session and only an
  explicit handoff turns it off.
- [`docs/adr/0009-at-rest-encryption.md`](docs/adr/0009-at-rest-encryption.md)
  — the mandatory SQLCipher decision: declined for now, with the triggers that
  would reopen it.
- [`docs/adr/0010-styled-upload-file-inputs.md`](docs/adr/0010-styled-upload-file-inputs.md)
  — how a file input hidden behind a styled widget is still filled.

## CI

`.github/workflows/ci.yml` runs a matrix across **Windows, macOS, and Ubuntu**:

- Python: `ruff`, strict `mypy`, `import-linter`, full `pytest` with the
  section 19.1 coverage gates
- Frontend: `tsc --noEmit`, `vitest`, production `vite build`
- Rust: `cargo test --lib` with the generated allowlist and backoff tests
- Schema job: regenerates JSON Schemas and TypeScript types and fails on drift

Reproduce any CI job locally with the commands in
[Tests, lint, and generation](#tests-lint-and-generation).

## Security and privacy notes

- Local-only SQLite; no network calls on startup.
- `diagnostics.fetch_url` is a single explicit HTTP probe, not a crawler and not
  called by any UI flow today.
- Logs redact resume text, answer books, email addresses, phone numbers, and
  password fields (`app/infrastructure/logging`).
- The program never submits job applications: the browser provider has no
  click, press or evaluate capability, and submission requests are aborted for
  the whole life of a session (ADR 0008). Playwright is an optional extra; with
  it absent every `browser.*` call reports an actionable error.
- A source is archived, not deleted: its jobs, observations and crawl history
  stay in the database (`sources.delete`).
