# ADR 0010: Fill a recognised file input hidden behind a styled upload widget

- **Status:** Accepted (implemented)
- **Date:** 2026-11-22
- **Related:** spec §13.3 (`fillable_profile` includes resume/cover-letter file
  upload), §13.4.4 (upload primitive), §13.5.1 (capability-restricted handle),
  ADR 0008 (submission interceptor)
- `app/domain/services/form_filler.py`, `tests/safety/test_autofill_safety.py`,
  `tests/integration/test_browser_live.py`

## Context

The first M7 filler refused every CSS-hidden control, including file inputs.
That was stricter than the spec, and it was defensible for text fields: a hidden
input cannot be checked against what the user sees on the page. For a file
input, though, the refusal removed the product's headline feature on the
markup almost every ATS uses:

```html
<label class="upload-button">Attach your resume
  <input type="file" name="resume" class="invisible-resume-upload">
</label>
```

A human clicks the styled label, which opens the file picker for the hidden
input. The app cannot click, but `upload_file` calls Playwright's
`set_input_files`, which does not require the input to be visible. So the
refusal was not protecting a capability boundary — the capability already
existed and was allowed by §13.5.1 — it was discarding a recognised field.

The stated safety justification ("a hidden input could send your résumé
somewhere you did not intend") did not hold: a visible file input is equally
under the page's control, and the network guard (§13.5.4), not visibility, is
what stops the page from posting the application.

## Options considered

1. **Keep the blanket refusal.** Safe in the narrow sense, but resume upload
   silently never works on Lever, Greenhouse, Ashby or Workable, and the user is
   told to attach a file they have no way to reach without opening dev tools.
   Rejected.
2. **Allow every hidden file input.** Fixes the widget, but also writes a stored
   résumé path into a hidden input the app cannot name — exactly the honeypot
   case the blanket rule was for. Rejected.
3. **Allow only a file input the classifier positively recognised as the résumé
   or cover letter, at fill confidence.** Chosen.

## Decision

A hidden input stops being an automatic refusal only when all of these hold:

- `kind == "file"`;
- `classification.field_class == "fillable_profile"`;
- `classification.key` is `resume_file` or `cover_letter_file`;
- `classification.confidence >= 0.85` (the §13.3 fill threshold).

Every other hidden control keeps the refusal and its skip reason. The upload
still requires a stored answer-book path, and the write goes through the same
`upload_file` primitive, so there is no new capability. A page's POST that
follows the upload is still aborted for the life of the session (ADR 0008), and
no submission path is added.

## Consequences

- Résumé and cover-letter upload work on the styled-widget markup that real ATS
  pages use; `tests/integration/test_browser_live.py` drives a hidden input with
  a real Chromium and asserts the file is set and zero posts are made.
- The honeypot defence becomes classification-based for file inputs: an
  unrecognised hidden file input is never written to, however it is labelled.
  A honeypot deliberately labelled "Resume" is indistinguishable from a styled
  widget, and the user's own reviewed plan names the file and the field.
- The docs no longer promise a domain-wide refusal; they describe the narrower
  rule instead.
- This does not change a §13.5 safeguard, so it does not need maintainer
  approval under §22.15. ADR 0008's interceptor lifetime still does.
