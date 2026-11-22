# ADR 0009: At-rest encryption — SQLCipher is declined for now

- **Status:** Accepted (2026-11-22)
- **Date:** 2026-11-22
- **Related:** spec §18.2 (privacy commitments), §18.3 (optional hardening),
  §22.6 (this ADR is mandatory), §14.1 (database settings), ADR 0003 (PDF
  licensing is the precedent for a distribution gate)
- `app/infrastructure/persistence/sqlite/uow.py`,
  `app/infrastructure/persistence/sqlite/migrator.py`

## Context

§22.6 makes a SQLCipher decision mandatory, to be recorded whether or not
encryption is adopted. §18.3 lists at-rest encryption as *optional* hardening
"because it affects performance and recovery". The database holds resume text,
contact details, application history and the autofill answer book: the most
sensitive data the app stores. It is a single SQLite file in the per-user
application data directory, opened only by the worker.

The threat model (§18.1) is a local attacker or another local process, not a
remote one: there is no account, no sync and no telemetry, so nothing leaves the
machine unless the user exports it.

## Options considered

1. **SQLCipher via a Python binding.** Strongest at-rest story, but it needs a
   native build per platform (the same distribution gate that ADR 0003 records
   for PyMuPDF), a key-management story for every installer, and a migration of
   existing plaintext databases. It also breaks the "no external SQLite install"
   property README documents, and it would have to ship before M11's installer
   work to be honest about it.
2. **Encrypt the sensitive columns or files in the application layer.** Resume
   originals are already content-addressed files; encrypting them needs a key
   held in the OS keychain (the spec suggests the Tauri keychain plugin). This
   protects resumes but leaves the database — which contains extracted resume
   text and answers — in the clear, so it is a half-measure unless combined
   with option 1.
3. **Rely on OS full-disk encryption and say so.** FileVault, BitLocker and
   LUKS are the default on current installs of the target platforms. The app
   documents the requirement instead of pretending to provide confidentiality
   itself.
4. **Do nothing and do not document it.**

## Decision

Adopt **option 3 for this release**: no SQLCipher, no per-file encryption.
Settings → Privacy states plainly that the database is a local file protected by
the operating system's disk encryption, and that the app itself does not encrypt
it. Resume originals and the database live under the per-user application data
directory, which is the protection boundary.

This is a deliberate deferral, not an omission. The mandatory ADR is satisfied
by recording the decision and its triggers.

## Consequences

- A process running as the same user can read the database and resumes. That is
  the accepted risk for a single-user desktop app on an encrypted disk.
- `system.export_data` (M11) must warn that an archive is **not** encrypted and
  is protected only by wherever the user puts it.
- The worker never writes resume text or answers to logs (already enforced and
  tested), so log rotation does not widen the exposure.
- Performance and recovery stay simple: no key, no unlock prompt, no lost-data
  failure mode, and a database the user can copy or inspect.

## Revisit when

- the app gains sync, multi-user profiles, or a shared machine deployment;
- M11's security review recommends it;
- a Tauri keychain plugin is already a dependency for another reason; or
- SQLite's encryption story becomes buildable with the permissive toolchain the
  project already requires (the ADR 0003 gate).
