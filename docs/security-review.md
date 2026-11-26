# Security review

Scope: the local desktop app and its worker sidecar, milestone M11. This is a
working review, not a third-party audit.

## Assets and trust boundaries

| Asset | Where it lives | Trust boundary |
| --- | --- | --- |
| Job postings, applications, résumé text | local SQLite under the data directory | untrusted text, never executed |
| Application answers and snippets | local SQLite | user-entered; typed only into forms the user opened |
| Backups | `backups/` in the data directory | untrusted on import; validated and path-scoped |
| Network responses | worker HTTP client | untrusted; parsed, never rendered as HTML |
| IPC envelopes | WebView ↔ supervisor ↔ worker | validated at each hop |

The worker is a child process of the supervisor. The WebView talks to the
supervisor through Tauri commands and to the worker only through allowlisted
methods (`frontend/src-tauri/src/lib.rs` plus `schemas/ipc/methods.json`).
Submission-capable methods are never on the allowlist.

## Controls and evidence

### Untrusted text is never rendered as HTML

Job descriptions are shown through `JobDescription`, which renders text nodes.
No frontend source calls `dangerouslySetInnerHTML`, sets `innerHTML`, or uses
`document.write`; `tests/safety/test_html_xss.py` enforces that, and the corpus
in `tests/fixtures/xss/payloads.json` covers tags, event handlers, protocol
handlers, encoded payloads and oversized strings. `JobDescription.test.tsx`
asserts the payload text survives as literal text.

The rule is: **never render posting HTML**. There is no sanitizer to bypass,
because no posting HTML reaches the DOM.

### IPC fuzzing

`tests/safety/test_ipc_fuzz.py` sends malformed envelopes (missing method,
wrong types, oversized fields, unknown methods, uppercase method names, handler
exceptions) and asserts every answer is a structured response envelope with
`ok: false` and a stable error code. A missing or null request id is still a
response, not a crash. Params that are not an object are rejected before a
handler sees them.

### SSRF and URL handling

Discovery fetches only URLs derived from the user's sources and connector
templates. The HTTP client has byte, redirect and timeout ceilings. External
links opened from the UI go through Rust `open_external_url`, which accepts only
`http` and `https`. Original-file opening is restricted to paths inside the data
directory.

### Dependency audits

`ci.yml` runs:

- `pip-audit` against the installed environment,
- `npm audit --omit=dev --audit-level=high`,
- `cargo audit` (RustSec advisory database) for the Tauri crate.

Findings are fixed or recorded below with a reason. The workflow fails on new
high-severity findings; there is no blanket `continue-on-error`.

Results on 2026-11-26:

- `npm audit --omit=dev --audit-level=high`: 0 vulnerabilities.
- `pip-audit -r` over the project's declared runtime and dev requirements:
  no known vulnerabilities.
- A bare `pip-audit` of the developer's global Windows environment reported old
  `setuptools`, `filelock` and `dnspython` from unrelated global tooling (plus
  a CPU-only `torch` it could not audit). Those packages are not Job Signal
  dependencies and are not copied into the frozen worker; the project-resolved
  audit is clean and CI audits a fresh environment. This is a recorded
  environment exception, not a suppression in the project.

RustSec results (`cargo-audit 0.23.0` against the RustSec database of
2026-11-26, 478 crates in `frontend/src-tauri/Cargo.lock`):

- The first run found two vulnerabilities. Both were fixed in the lockfile,
  which is part of this change, and the re-run reports none:
  - `RUSTSEC-2026-0594` (`cookie` 0.18.2, private cookie nonces can repeat after
    a fork, medium 6.5) → `cookie 0.18.3`.
  - `RUSTSEC-2026-0592` (`url` 2.5.8, stack overflow in `Url::origin()` on
    nested `blob:` URLs) → `url 2.5.9` with `form_urlencoded 1.2.3`.
- Six advisory warnings remain and are recorded exceptions, not suppressions:
  `RUSTSEC-2026-0543` (`chrono` unmaintained), `RUSTSEC-2024-0370`
  (`proc-macro-error` unmaintained), `RUSTSEC-2026-0370`/`-0371`/`-0372`
  (`zvariant`, `zbus_names`, `zvariant_derive` unmaintained) and
  `RUSTSEC-2024-0429` (`glib` 0.18 unsoundness in `VariantStrIter`).
  All six are transitive, none has an upgrade that keeps Tauri 2 working
  (the `glib`/`zvariant` path is Tauri's Linux GTK/zbus backend and the
  advisory covers an API Job Signal never calls), and none is reachable from
  the app's own code on Windows or macOS. `cargo audit` exits 0 on warnings, so
  the CI gate still fails on any new vulnerability.

Local `cargo audit` after the fix: exit 0, "6 allowed warnings found".

### Data at rest and deletion

The database is local and unencrypted by design (ADR-0009). Delete-all-data
requires the typed word `DELETE`, stops the worker first, deletes only the data
directory contents, and restarts the worker (ADR-0017). The preview build
refuses and explains where the feature exists.

### Backups

Exports are portable JSON with a checksum and row counts. Imports verify table
names, reject paths outside `backups/`, cap file size, and re-count rows before
reporting success. Scheduled archives retain a configurable number and never
prune manual exports.

## Residual risks

- Untrusted job text can still mislead a human reader. Copy avoids certainty
  claims ("proves", "scam"), and the language check enforces the vocabulary, but
  no classifier is claimed.
- The local database is readable by anyone with access to the user's account;
  full-disk encryption is the operating system's responsibility.
- Dependency audits cover known advisories only; they cannot prove absence.
- The macOS build is not signed or notarized in this Windows-only review; the
  release checklist keeps that as a release gate.

## Sign-off checklist

- [x] No raw-HTML sink in frontend source; XSS corpus passes.
- [x] IPC fuzz corpus returns structured errors only.
- [x] External URL allowlist and data-directory path checks tested.
- [x] Typed delete-all-data confirmation and path-scoped wipe tested, including
      the Settings card itself (`SettingsDeleteData.test.tsx`).
- [x] Delete-all-data exercised end to end against the real frozen worker:
      rejected confirmations change nothing; `DELETE` exits the old process,
      wipes only the data-directory contents, preserves a sibling directory,
      restarts the worker, and recreates schema 18. See ADR-0017 for evidence.
- [x] Backup import validation and scheduled retention tested.
- [ ] macOS codesign/notarization verified on macOS CI (release gate).
- [x] Dependency audits recorded for this change: `pip-audit` and
      `npm audit --omit=dev --audit-level=high` clean, RustSec findings fixed in
      `Cargo.lock` or listed above as reasoned exceptions. The CI `audits` job
      re-runs all three on the release commit.
