# ADR 0008: A session-long submission interceptor, with an explicit user handoff

- **Status:** Accepted (approved by the maintainer on 2026-11-22, per spec §22.15)
- **Date:** 2026-11-22
- **Related:** spec §13.4.5 (the user submits), §13.5.4 (network guard),
  §22.15 (changing a §13.5 safeguard needs an ADR), ADR 0007
- `app/infrastructure/browser/playwright_provider.py`,
  `app/application/commands/browser.py`, `browser.handoff`

## Context

The spec contains two requirements that pull against each other once a real
browser is involved:

1. **§13.5.4**: the Playwright context installs an interceptor that aborts
   submission-shaped `POST/PUT/PATCH` requests, so the app cannot submit an
   application, "not automatically or semi-automatically".
2. **§13.4.5**: "The browser stays open. The user reviews and submits
   manually." A human click on the page's Submit button has to work, in the
   window the app opened.

The first implementation armed the interceptor only while `FormFiller.apply`
was writing, plus a two-second grace window for deferred page handlers. That
satisfied (2) trivially, but it violated (1) in the case the interceptor exists
for: a page that reacts to a filled field with a *delayed* submit —
`setTimeout(() => fetch('/apply', …), 5000)`, a debounced auto-save that posts
the form, or a handler waiting on an async validation call. The automation
caused the write that scheduled the request, and once the window closed the
request went through.

Any time-boxed window has the same flaw: the bound is a guess about the page's
timing, and a hostile or merely slow page chooses it, not us.

## Options considered

1. **Keep the short window and grow the grace period.** Still a guess; a
   longer guess blocks the user for longer after every fill and never covers an
   unbounded delay. Rejected.
2. **Intercept for the whole session and never disarm.** Fully satisfies
   §13.5.4 and makes the automation airtight, but breaks §13.4.5 outright: the
   user can never submit in that window, which makes autofill useless.
3. **Attribute requests to trusted user gestures** (init script + exposed
   binding checking `event.isTrusted`) and allow only gesture-attributed
   submission requests. Principled, but the binding lives in the page's realm,
   so page script can call it and forge a gesture. It also needs an init script
   and CDP-level plumbing whose failure modes are hard to test. Rejected as
   forgeable and disproportionate.
4. **Intercept for the whole session; disarm only on an explicit user
   handoff.** Chosen.

## Decision

1. **The interceptor is armed when a session's page opens and stays armed for
   the life of the session.** No timers, no "automation window". Any
   submission-shaped request from that context is aborted and counted, however
   long after a fill it fires.
2. **Every fill re-arms it** (`FormFiller.apply` cannot run without the use case
   calling `rearm` first). A page can therefore never be in the state "values
   written by the automation, submissions allowed".
3. **The only exit is `browser.handoff`**, an explicit user action in the
   autofill panel ("I'll submit in the browser"). It disarms the interceptor for
   that session and returns `interceptor_active: false`. It submits nothing, has
   no parameters beyond the session id, and is not reachable from any fill path.
4. **The provider still has no generic click, press or evaluate**, step
   navigation still refuses outright, and `browser.submit` / `browser.click`
   still do not exist. This ADR changes only the interceptor's lifetime.

## Consequences

- §13.5.4 is satisfied more strictly than the text requires: the guard is on for
  the whole session instead of for a window around the write. A test drives a
  page whose POST fires 2.5 s after the input event and asserts zero
  submissions (`tests/integration/test_browser_live.py`).
- §13.4.5 keeps working through one extra click that names the user's intent,
  which is also a clearer UI: the panel states that the guard is off and that a
  further fill turns it back on.
- A user who fills, hands off, then fills again has to hand off again. That is
  the safe direction to fail: the guard defaults to on.
- The UI gets one new IPC method (`browser.handoff`) and the safety suite
  asserts it exists, cannot submit, and that no `AUTOMATION_GRACE_S`-style
  window reappears.
- **Approval:** per §22.15 this is a change to a §13.5 safeguard, recorded
  here rather than merged silently. The maintainer approved the session-long
  interceptor lifetime and the `browser.handoff` exit on 2026-11-22, so the
  decision is accepted and implemented; any later widening of the exit (a
  timer, a scripted click, a method that submits) needs a new ADR.
