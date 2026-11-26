# Privacy and security

## Where data lives

Everything is local. The Python worker keeps one SQLite database under the
app's data directory, together with resume files and crawl records. Nothing is
uploaded, and there is no account. The frontend talks to the worker over the
Tauri IPC bridge; it never opens a listening network port.

## What leaves the machine

Only outbound HTTP that a feature explicitly needs:

- crawling a source you added or auto-discovery selected, through the policy
  HTTP client (robots rules, rate limits, redirect re-validation);
- the polite endpoint check on a posting's application page, GET only, capped
  and budgeted per posting;
- a diagnostics URL fetch when you ask for one.

No telemetry, analytics or crash reporting is sent anywhere.

## Applications and forms

The app **never submits an application**. There is no `browser.submit`,
`browser.click`, `browser.press`, `browser.type`, `browser.evaluate` or
`applications.submit` method in the IPC catalogue, and tests assert the allowlist
and the Rust allowlist both refuse them. Autofill fills fields you review in a
headed browser; handing the window over so you can submit is an explicit user
action.

Demographic and legal questions are never auto-answered; they are left for you.

## Secrets and credentials

There are no cloud credentials in the project. Site logins are not stored by the
app; the browser session belongs to the user. API keys for any optional provider
belong in the user's environment, never in source, logs or generated assets.

## Data integrity

Posting intelligence history is append-only: `posting_signals`,
`signal_observations`, `risk_assessments`, `risk_model_versions`,
`classification_changes`, `company_hiring_summaries` and
`application_endpoint_observations` have SQLite triggers that abort `UPDATE` and
`DELETE`. A verdict can therefore be re-derived and audited.

Migrations are forward-only. Back up the data directory before upgrading if you
want a rollback path; see ADR-0012 for why updates are manual.

Every startup runs `PRAGMA quick_check`. A damaged database blocks startup with
instructions to restore from `<data dir>/backups`; the app does not accept new
writes into a file it cannot trust. Automatic backups run weekly by default and
keep the four newest automatic archives.

## Deleting everything

Settings → Data & backup → **Delete all local data** requires typing `DELETE`.
The supervisor stops the worker, waits for it to exit, removes every entry in
the local data directory (database, backups and logs), recreates the directory
and starts a fresh worker. Nothing outside that directory is touched. The
browser preview refuses and says the feature is only in the desktop app. See
[ADR-0017](adr/0017-delete-all-local-data.md).

The wider security review — IPC fuzzing, the XSS corpus, audits and residual
risks — is in [security-review.md](security-review.md).

## Reporting

This is a local tool with no server component. If you find a safety issue — for
example a code path that could submit an application — treat it as a release
blocker and add a regression test next to the code before fixing it.
