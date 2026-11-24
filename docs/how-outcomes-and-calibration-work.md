# How outcomes and calibration work

Two separate things live here:

1. **Outcome analytics** describes what happened after you applied, grouped by
   properties of the posting. It is descriptive, not predictive.
2. **Calibration** compares predicted posting risk with what endpoint checks
   actually observed. It can suggest a model change; it never applies one.

Nothing on this page changes ranking by itself. A weight change needs a new
model version that you save explicitly.

## Recording an outcome

On the Applications screen each tracked application has an outcome selector:
acknowledged, recruiter contacted, interview, rejection, offer, or
*no response*. Recording an outcome writes an append-only `application_outcomes`
row with `confirmed = true` and an `outcome_recorded` event. The app never writes
an outcome on your behalf, and it never infers one from silence.

**`no_response` is not a risk signal.** It is your note that no reply arrived.
Analytics records it, but the response rate counts only the outcomes in
`RESPONSE_KINDS` (`acknowledged`, `recruiter_contact`, `interview`, `rejection`,
`offer`, `accepted`). A test asserts `no_response` is excluded. It also cannot
raise a posting's risk score.

## Response analytics

`applications.get_analytics` returns, for a window of 365 days by default:

| Grouping | Why |
|---|---|
| Risk class at application time | Does credibility relate to replies? |
| Endpoint state | Was the page live when you applied? |
| Repost count (0, 1, 2+) | Do reposted roles behave differently? |
| Company tier | Is there enough company history to say anything? |
| Fit band | Does fit relate to replies? |
| Posting age when you applied | Do older posts reply less? |

Each row reports `n`, `successes`, a rate and a 95% Wilson interval. Groups with
fewer than **5** applications are returned with `insufficient: true` and the UI
shows "hidden (small sample)" instead of a rate. That threshold is
`MIN_SAMPLE` in `app/application/analytics.py`.

These are associations in your own history. The screens never say a signal
*caused* a reply, and the copy avoids causal language deliberately.

## Calibration

`posting_intelligence.get_calibration` compares, for postings that have both an
assessment and an endpoint observation:

- the predicted risk band (`0.0–0.2`, `0.2–0.4`, …),
- the share of those postings whose page was observed gone or carrying closure
  wording.

Blocked, timed-out and unchecked pages are excluded, because a missing check is
not evidence. Each band shows `n` and a Wilson interval, and bands with fewer
than 5 observations hide their rate.

A **proposal** is returned only once at least **30** observations exist
(`CALIBRATION_MIN_SAMPLE`). The proposal suggests a prior risk equal to the
observed closure rate and states that saving it requires confirmation. Accepting
it is a `posting_intelligence.configure_model` call, which writes a new
`risk_model_versions` row; every earlier assessment keeps pointing at the version
that produced it. There is no automatic update and no hidden learning loop.

## Tests

- `tests/unit/test_analytics_rates.py` — small-sample hiding, Wilson bounds, and
  `no_response` excluded from responses.
- `tests/integration/test_outcome_analytics.py` — a confirmed interview counts as
  a response, `no_response` does not, and calibration withholds its proposal
  below 30 observations.
