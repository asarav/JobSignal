# How applications, suppression and autofill work

Job Signal never sends an application. It records what you applied to, hides
roles you have already covered, and — when you ask — fills the parts of an
application form that come from your own stored answers. You review the page and
submit it yourself.

## Application history

Tracking a job stores the job **as it looked when you applied**: title,
description, department, locations, workplace type and canonical URL. That
snapshot is what later suppressions are measured against, so a rewritten
posting cannot make an old application unrecognisable.

Statuses follow the spec: `interested`, `saved`, `applied`, `acknowledged`,
`recruiter_contacted`, `interviewing`, `rejected`, `withdrawn`, `offered`,
`accepted`. `interested` and `saved` are pre-application states: they never set
an application date and never hide anything. Every change writes an entry to an
append-only event log, so the timeline is history rather than a mutable status
field. Outcomes (`interview`, `rejection`, `offer` …) are separate records you
can add without rewriting the status.

## Why roles disappear from your results

When a job reaches `applied`, the rest of that company's stored postings are
evaluated against your history:

1. **Same job, reposted** — the same job id, a job linked as a repost, or the
   same canonical URL is hidden while that chain exists. Reason:
   `exact_repost_of_applied`.
2. **Similar role at the same company** — same title family and a weighted
   similarity (title, location, description, department) at or above the
   threshold (default 0.75) stays hidden until `applied_at + 180 days`. Reason:
   `similar_role_cooldown`.
3. **Every role at the company** — only if you turn the company-wide cooldown
   on; then it runs for 90 days. Reason: `company_wide_cooldown`.
4. **A different role** at the same company is allowed by default.

New postings are checked as they are ingested, an application change
re-evaluates its company, and expired decisions lapse the next time the
Applications or Previously Seen view is opened. Expiry only releases a job — it
deletes nothing.

Every suppression keeps its evidence: the application that triggered it, the
similarity breakdown, the policy values in force and a sentence explaining it.
The Applications screen shows that sentence, the expiry date and a **Restore**
button. Restoring is final: automation will not hide that job again for the same
application.

## What autofill will and will not do

Opening a job's application page launches a browser window you can see and
control. The app inspects the form and builds a plan *before* touching the page;
the review panel lists every field with its decision:

- **Filled** — name, email, phone, address parts, current location, profile
  URLs (LinkedIn, GitHub, website, a video link), current employer and title,
  education, years of experience, résumé/cover-letter upload. Only when the
  classification is confident and your answer book has a value.
- **Left blank** — demographic self-identification (gender, race, age range,
  veteran status, disability, pronouns), legal attestations and consents,
  signatures, national IDs, bank details, passwords, and every open question
  ("Why us?").
  These are blocked by the classifier before any answer is considered, so
  storing an answer cannot unlock them.
- **Left blank** — anything unrecognised, any honeypot or CSS-hidden input
  that is not a recognised upload, and any field the filler could not verify
  after writing.
- **Your own answers only** — work authorization, sponsorship, notice period,
  start date, salary expectation and relocation are filled *only* if you stored
  that exact answer in **Settings → Autofill answers** (`autofill.answers`).
  Nothing is inferred, and a stored answer cannot unlock a blocked field.
  Every key the classifier can produce has a field in that editor, so a
  recognised question is answered or left blank — never quietly ignored for
  want of a place to store the answer.

Open questions are answered only from a saved answer you mapped yourself
(**Settings → Answers to open questions**). The saved question is compared with
the field's own label, and the answer is typed only into a field the classifier
placed in `free_text_unknown` whose question is that same text — case, spacing
and punctuation are ignored, so "Why us?" and "why us" are one question. A
saved answer never overrides a blocked class: an answer whose question matches
"What is your gender?" is still never used. Snippets stored by an earlier build
under an internal field id keep working.

A multiple-choice question (radio buttons) is resolved against its own options:
the stored answer is compared, ignoring case, with each option's value and
visible label, and the option that matches is the one selected. A stored "yes"
picks an option whose value is `Yes`. When nothing matches, the field is left
blank with a reason that lists the options, rather than forcing a value the
page does not offer. Each option is still only ever selected because you stored
that answer; nothing is chosen on your behalf.

Labels are read the way a browser associates them: an explicit `for=`, then
`aria-label` / `aria-labelledby`, then the nearest wrapping `<label>` — even
when the control sits a few elements inside it, which is the markup most ATS
builders emit (`<label><div>Full name</div><div><input></div></label>`). A
label that wraps more than one control is ambiguous, so it contributes no
label rather than lending its text to one field.

Some cards put the question *beside* the control list instead of in a label.
Lever's "Are you eligible to work in the United States?" is a `<div>` above two
`Yes`/`No` checkboxes, and each box's own label can only say "Yes". When a
sibling block marks itself as a label or question, the inspector uses it to name
the choice group, so the field is recognised as work authorization instead of
being left unrecognised. That changes recognition, not permission: the answer
still comes only from what you stored, and each option box is matched on its own
so a stored "yes" ticks the `Yes` box and never the `No` one. A stored "no"
ticks `No` — it is your answer, not an inference. A section heading such as
"Links" is never taken for a question, because it does not mark itself as one.

A file input that the page hides behind a styled upload widget (`class="…
invisible-resume-upload"`) is still the résumé control: when the classifier
positively recognises it as the résumé or cover letter and you stored a path,
the file is attached to that input. Clicking the styled button is a click the
app cannot make, but setting the input's files does not need one. The exception
is narrow: only those two recognised uploads may be written through a hidden
element, and any other hidden control — including a file input the classifier
cannot name — is skipped with *unrecognised hidden file input: attach the file
yourself in the browser* and never touched. Nothing is submitted either way.

Opening a job reuses the window already open for it, and **Re-check form**
re-inspects the current DOM instead of reopening the page. That is what makes
multi-step forms work: you advance the step yourself, re-check, review the new
plan, and fill again. One step is bounded to `autofill.max_fills` writes (200 by
default) and paced by `autofill.fill_delay_ms` (40 ms) so a pathological form
cannot keep the worker busy or fill instantly.

A filled value is read back twice: once right after the write, and once at the
end of the pass. Some widgets accept a value and then restore their own —
Lever's "Current location" resets itself on blur unless a suggestion from its
dropdown was picked, and picking one needs a click this app does not have. Those
fields are reported as *the page did not keep the value (pick it yourself in the
browser)* rather than counted as filled, so the plan cannot claim a value the
page has already discarded. A plain-text location field fills normally.

Saving the answer book takes effect immediately; the worker keeps a live config
holder, so no restart is needed.

If the page turns out to be a login wall (`auth_required`) or a CAPTCHA
(`browser.challenge`), the app stops before touching the form and asks you to
proceed yourself. It never types credentials or solves a challenge.

## The no-submit design

Submitting is not a feature that is switched off; there is no code path for it:

- The page handle exposes `fill_text`, `select_option`, `set_checked`,
  `choose_radio`, `upload_file`, `read_value`, `snapshot_dom` and `screenshot`.
  It has no generic click, press, evaluate or dispatch.
- Step navigation (`click_step_control`) is disabled by default and refuses
  outright in this build.
- A submission lexicon rejects any control whose text names submitting, and the
  guard refuses Enter and newlines in single-line inputs.
- The browser context aborts POST/PUT/PATCH requests that look like an
  application submission, for the whole life of the window — not just while the
  filler is writing. A page handler that posts on `input`, immediately or
  seconds later, is blocked; every fill re-arms the guard. When you are ready
  to submit, **I'll submit in the browser** turns the guard off for that window
  (`browser.handoff`); filling again turns it back on. The app never sends the
  request, and the handoff method submits nothing (ADR 0008).
- A CI-blocking safety suite runs every fixture layout (including a multi-step
  form and adversarial labels), asserts zero submission requests, zero ticked
  consents, zero filled demographic fields, and scans the code for forbidden
  browser APIs. `browser.submit` and `browser.click` do not exist as methods.
- A real Playwright run against a local fixture server counts every POST. It
  asserts zero submissions from autofill, that the page's own auto-submit was
  aborted, that a POST delayed 2.5 s is aborted too, that a human click after an
  explicit handoff still submits, that hostile `id`/`name` attributes cannot
  redirect a fill, and that a login wall is reported rather than filled
  (`tests/integration/test_browser_live.py`).
- Stress tests cover 3,000-field forms, 600-option radio groups, 2,000-deep
  nesting, quote/newline-laden attributes and repeated plan application
  (`tests/safety/test_autofill_stress.py`).

Autofill needs the optional browser dependency:

```
pip install "lodestar[render]"
python -m playwright install chromium
```

Without it, `browser.*` calls report `browser.unavailable` and everything else
keeps working. The live safety test skips when Playwright or Chromium is
absent; the rest of the suite still runs.
