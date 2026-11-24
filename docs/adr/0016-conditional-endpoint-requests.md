# ADR-0016 — Conditional requests on the endpoint read path

- **Status:** Accepted (M11)
- **Date:** 2026-11-24
- **Supersedes:** nothing

## Context

The endpoint verifier (`jobs.verify_now` and the maintenance pass) re-reads a
posting URL to classify it as live, closed, login-walled or unknown. Most
re-verifications land on a page that has not changed since the last read, so
every one of them pays for a full body transfer against a third-party site that
we are a guest on. The plan's M11 section asks for ETag/conditional-request
caching on this read path, with ETag preferred over `Last-Modified`.

## Decision

1. **Store the validators we were given.** Each `application_endpoint_observations`
   row keeps the response status, final URL, classification, a deterministic
   content fingerprint and — in `details` — the `etag` and `last_modified`
   response headers. No new table and no new migration is needed; the observation
   already is the per-read record.
2. **Prefer `ETag`.** Before a read, the verifier looks up the latest observation
   for the job. If it carries an `etag`, the request sends
   `If-None-Match: <etag>`; `If-Modified-Since` is not sent as well. Only when
   there is no `etag` but there is a `last_modified` does it send
   `If-Modified-Since: <last_modified>`.
3. **A `304 Not Modified` reuses the previous state.** The verifier does not
   fetch or reclassify anything: it records a new observation with the `304`
   status, the previous classification and fingerprint, and
   `details.conditional = "not_modified"` plus `details.reused_state`. The
   user-visible classification therefore does not flicker, and the audit shows
   that the answer came from a revalidation rather than a fresh body.
4. **Every other status is handled as before.** A `200` reclassifies from the
   body and replaces the stored validators; errors and redirects follow the
   existing classification rules. Redirects are still re-validated by the policy
   client, and only `GET` is ever used.
5. **No conditional headers on the first read.** With no stored observation the
   request is unconditional, so a new job cannot be skipped because of a stale
   validator.
6. **Policy limits still apply.** Conditional requests count against the same
   per-job 7-day budget and the same robots/host policy as any other read; a
   `304` is cheap but is not free.

## Consequences

- Repeat verification traffic drops to a header exchange when the page is
  unchanged, which is the common case for a live posting.
- The observation table grows one row per check, as before; the audit remains
  append-only.
- A site that changes content without changing its validators can serve a `304`
  we act on. That is the HTTP contract, and the next unconditional read (or a
  changed validator) corrects it.
- `details` carries the validators, so the ETag precedence rule is testable
  from stored data without mocking the network (`tests/integration/
  test_endpoint_conditional.py`).
