# IPC parity: UI calls, preview handlers and the real worker

Checked 2026-11-24 against the current working tree. The browser preview is a
development convenience, not the production data path. This report records how
every UI request was matched to a real worker method and how the real worker was
exercised with a seeded SQLite database.

## Summary

| Surface | Count | Check |
|---|---:|---|
| `WorkerClient` methods | 86 | 84 data wrappers map to a catalogued method; `request` and `onEvent` are infrastructure. |
| Methods in the TypeScript `WorkerMethod` union | 87 | Exactly equal to `schemas/ipc/methods.json`. |
| Real IPC methods in the catalog and dispatcher | 87 | Catalogue, generated schema and dispatcher agree. |
| UI call sites (requests, excluding event subscription) | 123 | Object keys are checked against the generated request type for each wrapper. |
| Distinct real methods the UI calls | 74 | Every one is registered by the real worker. |
| Preview bridge handlers | 56 | Every case name exists in the real catalog. The 55 successful results validate against that method's real response schema; `system.import_backup` is an intentional preview error. |

The static checks are in `frontend/src/services/ipcParity.test.ts` and
`frontend/src/services/previewSchemaParity.test.ts`. The real worker run is
`tests/integration/test_workflow_parity.py`.

## Cockpit contracts checked here

The power-user cockpit changed request and response shapes without adding IPC
methods. `applications.list` now takes `view`, `query`, `sort`, `limit` and
`offset`, and returns a paged list, the filtered `total` and a whole-history
`summary`; each `ApplicationInfo` also carries `company_name`. `jobs.list_saved`
takes `view` (`ready`/`all`), `query`, `sort`, `limit` and `offset`, and reports
the filtered total with `tracked` on each row. `jobs.ranked` takes `min_fit` and
`risk_classes`, and both stores accept `first_seen_after` for new-match
presets. The preview bridge mirrors those shapes:

- `applications.list` returns every summary field, resolves the preview company
  name on rows, and filters/pages the sample applications.
- `jobs.list_saved` distinguishes ready from all, pages the rows and marks
  tracked roles.
- `jobs.ranked` applies the fit/risk display filters and the first-seen cutoff.

The generated request and response schemas are checked by
`previewSchemaParity.test.ts`; the real worker volume path is exercised by
`test_volume_contracts_are_paged_and_report_true_totals` (see below).

## Request-shape audit

The UI call sites below were parsed from the TypeScript AST, not from a hand
list. Each argument object was checked field-by-field against the generated
request interface, which is generated from the Pydantic parameter model:

```bash
cd frontend
npx vitest run src/services/ipcParity.test.ts
```

Result: **0 invalid fields** across 123 calls.

| Real method | Calls | Screens |
|---|---:|---|
| `applications.create` | 2 | features/jobs/JobDetailRoute.tsx:126<br>features/saved/SavedRoute.tsx:186 |
| `applications.get_analytics` | 2 | features/applications/ApplicationsRoute.tsx:276<br>features/applications/ApplicationsRoute.tsx:433 |
| `applications.get_suppression_policy` | 1 | features/applications/ApplicationsRoute.tsx:261 |
| `applications.get_suppressions` | 2 | features/applications/ApplicationsRoute.tsx:260<br>features/seen/SeenRoute.tsx:35 |
| `applications.list` | 6 | components/Navigation.tsx:67<br>features/applications/ApplicationsRoute.tsx:229<br>features/home/HomeRoute.tsx:122<br>features/jobs/JobDetailRoute.tsx:93<br>features/jobs/JobDetailRoute.tsx:128<br>features/workflow/useWorkflowState.ts:48 |
| `applications.record_outcome` | 1 | features/applications/ApplicationsRoute.tsx:287 |
| `applications.restore_suppressed_job` | 2 | features/applications/ApplicationsRoute.tsx:328<br>features/seen/SeenRoute.tsx:53 |
| `applications.update_status` | 2 | features/applications/ApplicationsRoute.tsx:308<br>features/jobs/JobDetailRoute.tsx:121 |
| `applications.update_suppression_policy` | 1 | features/applications/ApplicationsRoute.tsx:344 |
| `browser.autofill` | 1 | features/jobs/AutofillPanel.tsx:80 |
| `browser.autofill_plan` | 1 | features/jobs/AutofillPanel.tsx:60 |
| `browser.close_session` | 1 | features/jobs/AutofillPanel.tsx:112 |
| `browser.handoff` | 1 | features/jobs/AutofillPanel.tsx:99 |
| `browser.inspect_form` | 1 | features/jobs/AutofillPanel.tsx:59 |
| `browser.open_job` | 1 | features/jobs/AutofillPanel.tsx:55 |
| `companies.get_detail` | 1 | features/companies/CompaniesRoute.tsx:38 |
| `companies.get_merge_history` | 1 | features/companies/CompaniesRoute.tsx:43 |
| `companies.list` | 2 | features/companies/CompaniesRoute.tsx:35<br>features/keywords/KeywordsRoute.tsx:67 |
| `companies.merge` | 1 | features/companies/CompaniesRoute.tsx:70 |
| `companies.unmerge` | 1 | features/companies/CompaniesRoute.tsx:81 |
| `crawl_runs.list` | 2 | features/home/HomeRoute.tsx:126<br>features/workflow/useWorkflowState.ts:51 |
| `diagnostics.sleep` | 1 | features/diagnostics/DiagnosticsRoute.tsx:54 |
| `discovery.auto_discover` | 1 | features/settings/SettingsRoute.tsx:283 |
| `discovery.run` | 4 | features/settings/SettingsRoute.tsx:247<br>features/settings/SettingsRoute.tsx:269<br>features/workflow/CollectJobsAction.tsx:44<br>features/workflow/CollectJobsAction.tsx:61 |
| `jobs.get_detail` | 2 | components/FocusQueue.tsx:85<br>features/jobs/JobDetailRoute.tsx:92 |
| `jobs.list` | 2 | features/discover/DiscoverRoute.tsx:230<br>features/search/SearchRoute.tsx:64 |
| `jobs.list_saved` | 7 | components/Navigation.tsx:66<br>features/home/HomeRoute.tsx:123<br>features/jobs/JobDetailRoute.tsx:94<br>features/saved/SavedRoute.tsx:145<br>features/saved/SavedRoute.tsx:152<br>features/saved/SavedRoute.tsx:153<br>features/workflow/useWorkflowState.ts:47 |
| `jobs.list_seen` | 1 | features/seen/SeenRoute.tsx:34 |
| `jobs.ranked` | 3 | features/discover/DiscoverRoute.tsx:211<br>features/home/HomeRoute.tsx:116<br>features/workflow/useWorkflowState.ts:43 |
| `jobs.set_user_state` | 4 | features/discover/DiscoverRoute.tsx:328<br>features/discover/DiscoverRoute.tsx:361<br>features/jobs/JobDetailRoute.tsx:142<br>features/saved/SavedRoute.tsx:211 |
| `jobs.unmerge` | 1 | features/seen/SeenRoute.tsx:69 |
| `jobs.verify_now` | 1 | features/jobs/RiskPanel.tsx:42 |
| `keywords.backfill` | 1 | features/keywords/KeywordsRoute.tsx:75 |
| `keywords.coverage` | 1 | features/keywords/KeywordsRoute.tsx:50 |
| `keywords.prevalence` | 1 | features/keywords/KeywordsRoute.tsx:49 |
| `keywords.trends` | 1 | features/keywords/KeywordsRoute.tsx:51 |
| `matching.apply_weight_proposal` | 1 | features/posting-intelligence/PostingIntelligenceRoute.tsx:103 |
| `matching.get_results` | 2 | features/jobs/FitPanel.tsx:30<br>features/resume/ResumeRoute.tsx:217 |
| `matching.get_weight_history` | 1 | features/posting-intelligence/PostingIntelligenceRoute.tsx:87 |
| `matching.get_weight_proposals` | 1 | features/posting-intelligence/PostingIntelligenceRoute.tsx:86 |
| `matching.run` | 6 | features/resume/ResumeRoute.tsx:331<br>features/resume/ResumeRoute.tsx:349<br>features/resume/ResumeRoute.tsx:372<br>features/resume/ResumeRoute.tsx:394<br>features/resume/ResumeRoute.tsx:425<br>features/resume/ResumeRoute.tsx:456 |
| `matching.update_weights` | 1 | features/resume/ResumeRoute.tsx:344 |
| `plugins.disable` | 1 | features/settings/SettingsRoute.tsx:190 |
| `plugins.enable` | 1 | features/settings/SettingsRoute.tsx:192 |
| `plugins.list` | 1 | features/settings/SettingsRoute.tsx:113 |
| `posting_intelligence.configure_model` | 1 | features/posting-intelligence/PostingIntelligenceRoute.tsx:63 |
| `posting_intelligence.evaluate` | 1 | features/posting-intelligence/PostingIntelligenceRoute.tsx:69 |
| `posting_intelligence.get_calibration` | 1 | features/posting-intelligence/PostingIntelligenceRoute.tsx:78 |
| `posting_intelligence.get_company_assessment` | 1 | features/posting-intelligence/PostingIntelligenceRoute.tsx:120 |
| `posting_intelligence.get_risk_history` | 1 | features/jobs/RiskPanel.tsx:23 |
| `posting_intelligence.get_signals` | 1 | features/jobs/RiskPanel.tsx:22 |
| `resume.get_profile` | 1 | features/resume/ResumeRoute.tsx:218 |
| `resume.import` | 1 | features/resume/ResumeRoute.tsx:303 |
| `resume.list_versions` | 4 | components/Navigation.tsx:65<br>features/home/HomeRoute.tsx:124<br>features/resume/ResumeRoute.tsx:203<br>features/workflow/useWorkflowState.ts:49 |
| `resume.set_active` | 1 | features/resume/ResumeRoute.tsx:318 |
| `resume.set_skill_status` | 1 | features/resume/ResumeRoute.tsx:362 |
| `resume.update_overrides` | 3 | features/resume/ResumeRoute.tsx:391<br>features/resume/ResumeRoute.tsx:409<br>features/resume/ResumeRoute.tsx:454 |
| `search_profiles.delete` | 2 | features/discover/DiscoverRoute.tsx:310<br>features/search/SearchRoute.tsx:115 |
| `search_profiles.list` | 4 | features/discover/DiscoverRoute.tsx:144<br>features/discover/DiscoverRoute.tsx:294<br>features/discover/DiscoverRoute.tsx:311<br>features/search/SearchRoute.tsx:26 |
| `search_profiles.save` | 2 | features/discover/DiscoverRoute.tsx:288<br>features/search/SearchRoute.tsx:92 |
| `settings.get` | 1 | features/settings/SettingsRoute.tsx:112 |
| `settings.update` | 2 | features/settings/SettingsRoute.tsx:302<br>features/settings/SettingsRoute.tsx:327 |
| `sources.add` | 2 | features/settings/SettingsRoute.tsx:264<br>features/workflow/CollectJobsAction.tsx:59 |
| `sources.delete` | 1 | features/settings/SettingsRoute.tsx:217 |
| `sources.list` | 4 | features/home/HomeRoute.tsx:125<br>features/settings/SettingsRoute.tsx:114<br>features/workflow/CollectJobsAction.tsx:28<br>features/workflow/useWorkflowState.ts:50 |
| `sources.set_enabled` | 1 | features/settings/SettingsRoute.tsx:205 |
| `sources.set_schedule` | 1 | features/settings/SettingsRoute.tsx:231 |
| `system.export_backup` | 1 | features/settings/SettingsRoute.tsx:148 |
| `system.get_backup_history` | 1 | features/settings/SettingsRoute.tsx:115 |
| `system.health` | 1 | stores/workerStore.ts:26 |
| `system.hello` | 1 | stores/workerStore.ts:25 |
| `system.import_backup` | 1 | features/settings/SettingsRoute.tsx:164 |
| `tasks.cancel` | 1 | features/diagnostics/DiagnosticsRoute.tsx:65 |
| `tasks.list` | 1 | features/diagnostics/DiagnosticsRoute.tsx:26 |

`workerClient.onEvent` subscribes to Tauri events and is deliberately not a
request method.

## Preview handler audit

All 56 preview cases are real method names. The preview result is checked
against the real response schema for the same method, including required fields
and forbidden extras:

```bash
cd frontend
npx vitest run src/services/previewSchemaParity.test.ts
```

Covered preview methods:

```text
applications.create
applications.get_analytics
applications.get_suppression_policy
applications.get_suppressions
applications.list
applications.record_outcome
applications.update_status
applications.update_suppression_policy
companies.get_detail
companies.get_merge_history
companies.list
companies.merge
companies.unmerge
crawl_runs.list
diagnostics.sleep
discovery.auto_discover
discovery.run
jobs.get_detail
jobs.list
jobs.list_duplicates
jobs.list_saved
jobs.list_seen
jobs.merge
jobs.ranked
jobs.search
jobs.set_user_state
jobs.unmerge
jobs.verify_now
keywords.extract
matching.get_results
matching.run
matching.update_weights
plugins.list
posting_intelligence.get_risk_history
posting_intelligence.get_signals
resume.get_profile
resume.import
resume.list_versions
resume.parse
resume.set_active
resume.set_skill_status
resume.update_overrides
search_profiles.delete
search_profiles.list
search_profiles.save
settings.get
settings.update
sources.add
sources.list
system.export_backup
system.get_backup_history
system.health
system.hello
system.import_backup
tasks.cancel
tasks.list
```

`system.import_backup` is intentionally an actionable error in preview because
the browser has no real database to restore. Its error code is checked; there is
no result shape to validate.

Preview handlers are samples, not a second implementation of ranking, matching
or persistence. Where a sample cannot be computed, the response uses the same
shape and an explicit sample note rather than inventing business data.

## Real worker execution

`tests/integration/test_workflow_parity.py` starts
`python -m app.worker.main` through the same NDJSON protocol the Tauri supervisor
uses. It seeds a temporary SQLite database with two companies, two jobs, one
source and two crawl runs, then imports a real resume file and walks the
workflow:

- Today: `crawl_runs.list` with `{limit: 1}`, `sources.list`,
  `jobs.list_saved`, `applications.list`, `resume.list_versions`.
- Profile: `resume.import`, `resume.list_versions`, `resume.get_profile`,
  `matching.run`.
- Find jobs: `jobs.ranked` with policy/sort/filters; `jobs.list`;
  `search_profiles.list/save/delete`.
- Job detail: `jobs.get_detail`, `jobs.set_user_state`, `jobs.list_saved`.
- Track: `applications.create`, `applications.list`,
  `applications.update_status`, `applications.record_outcome`,
  `applications.get_analytics`, `applications.get_suppressions`,
  `applications.get_suppression_policy`.
- Companies: `companies.list`, `companies.get_detail`,
  `companies.get_merge_history`, `companies.merge`, `companies.unmerge`, then
  history again after each decision.

The harness wraps every successful response in `ValidatedWorker`, which validates
it against the method's Pydantic result model. A missing required field, an extra
field, or a changed type fails the test. The run asserts non-empty totals and
records for the workflow path (2 jobs, 1 source, 1 resume, 2 matching results,
1 saved job, 1 application, 2 then 1 then 2 companies).

```bash
python -m pytest tests/integration/test_workflow_parity.py -q
```

The same file includes a volume run against seeded SQLite: 250 applications and
60 saved roles (50 ready) through the real worker. It asserts a 25-row first
page and a disjoint second page, a true `total` of 250, the application summary
(`applied=250`, `active=220`, `follow_up=20`, `interviewing=10`, `offers=10`,
`closed=10`), resolved company names, a 20-row waiting-14+-days page, a search
total of 250, and saved ready/all totals of 50/60 with tracked rows marked.

## Mismatches found and fixed

1. **`crawl_runs.list` rejected the UI's `limit`.** Both Today and
   `useWorkflowState` send `{limit: 1}`, while the real method accepted
   `EmptyParams` and enforced `extra="forbid"`. Added `CrawlRunListParams`
   (`source_id`, bounded `limit`), passed the limit into the repository, and
   regenerated schemas and TypeScript types.
2. **`discovery.run` advertised the wrong response model.** The catalogue said
   `OkResult`, but the real crawl returns counters (`discovered`, `new`,
   `updated`, `skipped`, `removed`, `errors`, `gone`, truncation/error detail).
   Added `DiscoveryRunResult`, switched the catalogue to it, removed the
   preview-only `ok` field, and made the preview return the real counters.
3. **`resume.import` returned undocumented counters.** The real handler returns
   `experience`, `education`, `certifications` and `projects`; the result DTO
   rejected them. Added those fields to `ResumeImportResult`.
4. **Merge history names were preview-only.** The Companies audit displayed a
   company name when available, but the real history rows only had IDs. Added
   `merged_name` and `survivor_name` to real merge-history records and to the
   preview records.
5. **Preview-only response drift.** Removed `sources.list`'s extra `total` and
   `last_crawled_at`, `jobs.verify_now`'s extra `state`, company-detail fields
   absent from the real DTO, and `resume.import`'s extra `source_file`. Preview
   application create/update now return the full `ApplicationInfo` shape.
6. **Preview workflow gaps.** Added `applications.record_outcome` and
   `applications.get_analytics` so the workflow's tracking calls do not hit the
   preview bridge's unknown-method error.
7. **Preview list drift after the cockpit rewrite.** `applications.list` and
   `jobs.list_saved` previously ignored the new view/search/sort/paging
   parameters, and the preview application rows lacked `company_name`. The
   bridge now filters, pages and annotates the samples, including
   `first_seen_after` on ranked jobs, so the parity validator sees the same
   shapes the worker returns.

## Limits of this verification

- **The packaged Tauri desktop shell was not launched.** The environment has
  Rust and a previously built debug executable, and `cargo test --lib` exercises
  the allowlist/supervisor code, but this audit could not drive the native
  WebView window and its GUI handshake. The worker itself was run headlessly over
  the exact protocol that shell forwards.
- **Headed Playwright autofill was not exercised.** Playwright is an optional
  extra; the browser/autofill commands are registered and covered by the existing
  safety tests, but no real browser window was driven in this pass.
- **No live external job board was crawled.** Network-dependent collection is
  covered by deterministic fixture tests. The real worker's source list, crawl
  history and database-backed workflow were exercised; a fresh live crawl needs
  a real board and network access.
- **The browser preview remains sample-only.** It intentionally does not
  implement `browser.*` or real backup restore, and its data never proves the
  production worker path. Use the desktop app for those actions.
- Methods not called by the UI (for example `system.shutdown`, the browser
  lifecycle when Playwright is absent, and some posting-intelligence and keyword
  maintenance methods) are checked for registration and schema parity but were
  not each executed in this pass.
