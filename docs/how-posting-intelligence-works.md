# How posting intelligence works

Job Signal records two independent numbers for every posting it evaluates:
**risk** (how much the evidence suggests the posting may not represent active
hiring) and **confidence** (how much usable evidence went into that estimate).
They are deliberately separate. A high-risk, low-confidence posting is shown as
*needs verification*, never as a ghost job.

Nothing here is a probability, a prediction of your chances, or an accusation.
An old posting is not a ghost job, and missing data is never treated as evidence
of inactivity.

## What gets stored

Every evaluation appends rows; nothing is overwritten.

| Table | What it holds |
|---|---|
| `posting_signals` | The registry of signal definitions, their versions and default weights |
| `signal_observations` | One immutable verdict per provider: status, risk contribution, confidence, evidence, timestamps |
| `risk_model_versions` | The exact model configuration and its hash, one row per distinct configuration |
| `risk_assessments` | Risk, confidence, class, evidence sufficiency, the signal rows it references, and an input hash |
| `classification_changes` | Every transition between classes, with both assessment ids |
| `company_hiring_summaries` | One immutable hiring snapshot per company per day, with its sample tier |
| `application_endpoint_observations` | What the employer's own application page answered when checked |

Triggers on each history table abort an `UPDATE` or `DELETE`, so a verdict the
app showed you can always be re-read and re-derived.

## The signals

Weights are the initial `risk.heuristic_v1` heuristics. They are configurable
and visible; the app never changes them silently.

| Signal | Weight | Measures |
|---|---:|---|
| `signal.posting_age` | 0.10 | Time since first seen, against comparable roles when a large enough sample exists. Age alone is capped at 0.5 |
| `signal.repost_frequency` | 0.20 | Reposts, posting-date resets and ID/URL churn in the last year |
| `signal.endpoint_availability` | 0.25 | What the application page says: reachable, closed, gone, or a generic redirect |
| `signal.content_staleness` | 0.10 | Unchanged description over time, copied descriptions, boilerplate, missing specifics |
| `signal.duplicate_consistency` | 0.10 | Cross-source contradictions, e.g. one source says closed while another says active |
| `signal.company_hiring_activity` | 0.15 | Employer-level patterns, only when the company sample is large enough |
| `signal.explicit_closure_language` | 0.30 | "No longer accepting applications", "position has been filled" and equivalents, with negation handling |
| `signal.implausible_requirements` | 0.05 | Contradictory requirements, e.g. more years of a technology than it has existed |

A provider that lacks the data it needs returns **missing** with a reason. It
never returns a low-risk number as a substitute for absent evidence, and a
blocked page or a login wall is a statement about our access, not about the
posting.

### Endpoint checks

Endpoint verification asks the employer's own page whether the role is still
there. It is a polite `GET` through the same policy client as crawling (robots,
rate limits, size and time caps, redirect re-validation) and never submits,
clicks or fills anything. A single timeout is missing evidence; two consecutive
timeouts become a weak signal. Checks are budgeted: an active job at most once
per seven days, plus an explicit "Verify now".

The verifier is `jobs.verify_now` and the maintenance sweep. When a check
cannot run, `signal.endpoint_availability` reports missing and the other seven
providers carry the assessment; missing is never treated as negative evidence.

## Risk and confidence

With weights `w`, contributions `r`, confidences `c` and a weak prior
(`w0 = 0.15`, `r0 = 0.20`):

```text
M       = Σ w_i · c_i
risk    = (w0·r0 + Σ w_i·c_i·r_i) / (w0 + M)
floor   = max(r_i for strong signals) · 0.95
risk    = max(risk, floor)            when a strong signal is present

coverage    = Σ w (available) / Σ w (all registered)
quality     = Σ w·c / Σ w (available)
confidence  = 0.5·coverage + 0.5·quality   (at least 0.70 with a strong signal)
```

Contradictory evidence lowers confidence, never raises risk.

## Classes

| Class | Shown as | Rule |
|---|---|---|
| `likely_inactive` | Likely inactive / potential ghost job | risk ≥ 0.75 and confidence ≥ 0.50 |
| `likely_stale` | Possibly stale posting | 0.50 ≤ risk < 0.75 and confidence ≥ 0.40, or risk ≥ 0.75 with confidence 0.30–0.50 |
| `likely_active` | No strong sign of inactivity | risk < 0.30 and confidence ≥ 0.35 |
| `needs_verification` | Needs verification | everything else |

The thresholds are uncalibrated initial heuristics. One weak signal can never
lift a posting above "needs verification"; high-risk classes require
corroborating or strong evidence; "no strong sign of inactivity" requires some
evidence of activity, not merely the absence of red flags. The app never
adjusts thresholds on its own.

## Reproducibility

An assessment stores the ids of the exact signal rows that produced it, the
model version row and its config hash. Re-running the pure model over those
stored signals with the same configuration reproduces `risk`, `confidence` and
the class exactly (there is a test for this). Changing a weight creates a new
`risk_model_versions` row; earlier assessments keep pointing at the old one.

Re-evaluation happens when a new snapshot, repost or endpoint observation
arrives, on the daily pass for active jobs, and after an explicit configuration
change. If the inputs have not changed, the existing assessment is reused rather
than duplicated. "Latest" is read with an indexed `ORDER BY evaluated_at DESC`;
there is no separate read-model table yet, and one can be added later without
touching history.

## Company-level hiring intelligence

A company summary is written at most once per company per day. It reports raw
counts, and only labels a pattern when the sample is adequate:

| Tier | Condition | What you see |
|---|---|---|
| Insufficient | fewer than 3 roles, or window under 21 days | "Insufficient evidence", raw counts only |
| Limited | 3–7 roles, or window under 60 days | descriptive counts, no labels |
| Adequate | at least 8 roles and a 60-day window | cautious labels such as "Repeated reposting observed" or "Elevated posting risk" |

Labels never imply intent. There is no "scam", "fake" or "deceptive" anywhere in
the copy, and every label links to the postings behind it.

## Endpoint verification limits

`jobs.verify_now` and the maintenance sweep use the same polite verifier. It:

- fetches with **GET only**, through the policy HTTP client (robots rules, rate
  caps, redirect re-validation, no credentials);
- keeps a **7-day per-posting budget**, so pressing "Verify now" twice does not
  fetch twice;
- records one observation per check (`ok`, `gone_404_410`, `closed_message`,
  `auth_required`, `blocked`, `generic_redirect`, `form_unavailable`, `timeout`,
  `error`).

`classify_endpoint(status, final_url, html)` is pure and unit-tested. A timeout
is recorded as `timeout` and counts as *missing evidence*: one failure never
closes a posting and never raises risk. A blocked page or a login wall is
recorded the same way.

The verifier never interacts with the page. There is no click, no typing, no
form fill and no submission anywhere in this path, and a test asserts the
recorded request is a GET without a body.
