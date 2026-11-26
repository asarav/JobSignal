# Release checklist

Automation covers the mechanical checks. A person still has to judge what a
headless job cannot see.

## Automated by CI

| Check | Where |
| --- | --- |
| Python tests, coverage gates, ruff, mypy, import-linter | `ci.yml` → `python` |
| Frontend typecheck, Vitest, production build | `ci.yml` → `node` |
| Rust unit tests and clippy | `ci.yml` → `rust` |
| Schema and TypeScript generation freshness | `ci.yml` → `schema` |
| Markdown links, ADR references, M0–M11 coverage | `scripts/check_docs.py` |
| Dependency audits (`pip-audit`, `npm audit`, RustSec) | `ci.yml` → `audits` |
| Frozen sidecar handshake (`worker.ready` → `system.hello` → shutdown) | `desktop-build.yml` after each bundle |
| Delete-all-data end to end against the real sidecar | `desktop-build.yml`; `cargo test --test delete_all_data_e2e` |
| Windows MSI launch against an empty data directory | `desktop-build.yml` (Windows) |
| Linux AppImage launch against an empty data directory | `desktop-build.yml` (Linux) |
| macOS unsigned `.app` launch under a temp HOME | `desktop-build.yml` (macOS) |
| macOS `codesign --verify` and `spctl` when a certificate is configured | `desktop-build.yml` (macOS) |

The packaged smokes assert that `lodestar.sqlite` appears, the worker sidecar
sits beside the executable, and the schema version is readable. They briefly
open the app window.

## Human checks before publishing

- [ ] The window renders on each target OS (sidebar, Today, Discover).
- [ ] Installer UX: the MSI/NSIS/DEB/RPM/DMG opens, installs, launches and
      uninstalls without leaving the worker running.
- [ ] The installer's Add/Remove Programs entry and icon are right.
- [ ] A first run on a clean machine reaches a usable screen without a
      console error.
- [ ] Settings → Data & backup → Delete all local data: the warning reads
      clearly and the window reloads after the restart. (The deletion is covered
      by the automated end-to-end test above, and the card's gating, call,
      confirmation reset and refusal message by `SettingsDeleteData.test.tsx`;
      the human check is the copy and the packaged window.)
- [ ] A manual backup exports, restores into an empty profile and verifies.
- [ ] On macOS: the artifact is signed and notarized. Without notarization it is
      explicitly **unsigned** and must not be presented as verified.

## Release gates

- macOS signing and notarization, verified on macOS hardware.
- Dependency audits on the release commit are recorded; any exception is
  documented in `docs/security-review.md`.
- No open safety issue that could submit an application or fill a form without
  review.

## Reporting

State exactly which automated checks ran, which packaged artifact was launched,
the observed schema version, and any check that was skipped. Do not report an
unsigned or unlaunched artifact as verified.
