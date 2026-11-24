# ADR-0012: auto-update is off by default

## Status

Accepted.

## Context

The app is a local-first desktop tool holding a resume, application history and
an encrypted-capable SQLite database. A background auto-updater would introduce a
network daemon that can replace binaries without the user watching, and it would
need a signing key and a hosted manifest. Neither exists in this build, and the
spec's §18.3 checklist treats background binary replacement as a security
boundary rather than a convenience.

## Decision

- Auto-update ships **disabled**. `tauri.conf.json` declares the bundle targets
  (MSI/NSIS on Windows, dmg on macOS, AppImage/deb on Linux) but no updater
  plugin and no endpoint.
- Updating is a manual action: download the new installer and run it. The
  database is forward-migrated on start and the previous installer can be kept
  for rollback by copying the data directory first.
- If auto-update is ever added, it must be opt-in, verify a signed manifest, and
  never run while a task is mid-flight. It will need its own ADR.

## Consequences

- No silent binary replacement, no update endpoint to secure or monitor.
- Users must check for releases themselves; the Diagnostics screen shows the
  running version and schema version to make that easy.
- Packaging stays reproducible with `tauri build`, and CI can assert the targets
  without a signing key for local builds.
