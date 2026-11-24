# How ranking works

Job Signal ranks postings on three inputs that stay separate all the way to the
screen. None of them is a probability that you will be interviewed, and the app
never presents them as one.

| Input | Where it comes from | What it answers |
|---|---|---|
| Satisfaction (fit) | `fit.weighted_v1` over six dimensions with your weights | How well the role matches what you want |
| Success evidence (credibility) | `1 − risk` from posting intelligence, plus company hiring evidence | How much the posting looks live and real |
| Actionability | freshness, endpoint state, suppression, salary and autofill readiness | Whether acting on it now makes sense |

Hard filters run first. A job that fails your profile's filters never reaches the
ranking step, so a high score cannot smuggle in a role you excluded.

## The score

```
credibility_factor = 1 − 0.5 · risk · min(1, confidence / 0.5)
rank_score         = fit · credibility_factor        (deprioritize)
rank_score         = fit                             (show_all, hide_high_risk, require_evidence)
```

Confidence scales the penalty. Risk 0.9 with confidence 0.1 barely moves a row;
risk 0.6 with confidence 0.8 moves it down noticeably. A missing assessment
leaves the factor at 1, because missing evidence is not negative evidence. A
missing fit score leaves `rank_score` empty and the row is ordered by recency
instead of being given an invented number.

## Policies

| Policy | Effect |
|---|---|
| `deprioritize` (default) | Multiplies fit by the credibility factor; nothing is hidden |
| `show_all` | Orders by fit only; risk is still displayed |
| `hide_high_risk` | Hides rows whose class is `likely_inactive` and confidence ≥ 0.5 |
| `require_evidence` | Shows only rows assessed as `likely_active` |

The policy belongs to the search profile and can be overridden per request. When
a row is hidden, it can still be shown with the "Show hidden" toggle, and it is
marked as hidden rather than silently dropped. Suppression (applied at a company
after a confirmed application) is separate and controlled by its own toggle.

## Why this rank

Every ranked row carries `rank_reasons`, the same strings the UI shows:

- the fit score, or an explicit "no fit score yet" note;
- the risk class with its risk and confidence;
- when `deprioritize` moved the row down, the factor that was applied, with a
  reminder that it was not hidden;
- when a hiding policy hid it, that fact.

The explanation comes from `app/domain/services/ranking.py` and is covered by a
policy-matrix unit test, so the UI cannot drift from the API.

## The ranking window

Ranking reads the most recent 500 postings that pass the filters (configurable up
to 2000 per request). The response reports `window` and `window_truncated`, and
Discover states it. This keeps the query bounded on a large database; a job
outside the window is still reachable through search. Moving to a fully indexed
SQL ranking query is a benchmark-driven follow-up, recorded in ADR-0011's
"no read model yet" decision.

## Fit is computed lazily

`jobs.ranked` joins whatever fit scores already exist. The Discover screen offers
"Compute fit", which evaluates fit for the rows in the window that do not have a
score yet (bounded to 200 per call) and then re-ranks. Fit results are cached by
input hash, so recomputing over unchanged inputs is cheap.

## What ranking does not do

- It does not learn from your outcomes automatically. Outcome associations are
  shown as associations and any weight change needs your explicit confirmation.
- It does not treat a missing signal as risk.
- It does not label a company or a posting as deceptive.
- It does not submit, click or type anywhere. The only network action in this
  pipeline is the polite endpoint fetch described in
  [how-posting-intelligence-works.md](how-posting-intelligence-works.md).

## Tests

- `tests/unit/test_ranking.py` — the policy matrix, factor maths and explanations.
- `tests/integration/test_ranked_jobs.py` — ordering, hiding, `include_hidden`
  and the reported window over a real in-memory unit of work.
- `frontend/src/components/RiskBadge.test.tsx` — the badge names the class and
  the confidence, renders nothing without evidence, and never shows a bare
  percentage.
