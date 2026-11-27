# How resume parsing and matching work

Importing a resume and scoring jobs runs entirely on your machine. No resume
text, contact detail or profile leaves the app: there is no network call in
either path (a privacy test blocks sockets and asserts nothing was sent).

## Import

Supported formats are PDF, DOCX and plain text. An imported file is copied into
a content-addressed store (`resumes/<sha prefix>/<sha>`), so the same file is
never stored twice, and the original file is never modified. Each import
creates a new immutable resume version on the candidate profile; the newest
version becomes active. Importing the same file again is allowed: the stored
copy is reused and a second version is recorded, so you can compare a re-export
of the same resume against the copy imported before it.

## What is extracted

| Field | How |
|---|---|
| Sections | Heading matching for experience, education, skills, projects, certifications, summary, contact (aliases such as "work history" and "technical skills" are recognised) |
| Experience entries | Split on date ranges such as `Jan 2020 - Present`, `01/2020 – 03/2022` or `2020 - 2021`; header lines and bullet lines are kept with their entry, and the header is split into employer and job title |
| Education | Institution, degree, field and dates from degree-bearing lines (`BSc`, `Master of Science in Physics`, `MBA`, `PhD`, …) |
| Certifications and projects | One entry per line or heading, with its bullet lines kept as details |
| Years of experience | Derived from merged, non-overlapping employment ranges; `Present` counts up to today; overlaps are counted once. Marked *inferred* |
| Contact | Name, email, phone, LinkedIn, GitHub and website. **Stored only** — contact details are never used in matching |
| Skills | Taxonomy v1 mentions, each labelled with an evidence level, the character spans that back it, and the time spent in roles that mention it (`duration_years` / `recency_years`) |

Dates are only recognised from month names with a year, numeric month/year, or a
bare year. Text such as "Python 3 - 5 years" is not treated as a date range.

### Skill evidence levels

| Evidence | Meaning | Matching strength |
|---|---|---|
| `explicit_with_experience` | Listed in Skills **and** written in the experience or project text | 1.0 |
| `explicit` | Listed in Skills, or written in a role but absent from the Skills list | 0.8 |
| `inferred` | Implied by the taxonomy (e.g. Django ⇒ Python) or mentioned only in the summary | 0.5 |
| `limited` | A single weak mention elsewhere in the resume | 0.3 |

A per-skill **user status** overrides the parser evidence:
`extensive_experience` counts 1.0, `have_experience` 0.9, `learning` 0.4,
`no_experience` 0, and `not_relevant` removes the skill from both numerator and
denominator. The extra expert level is a real answer rather than a label:
choosing it scores above "have experience", so existing have-experience answers
are rescaled from 1.0 to 0.9. Set it on the Resume screen; the next run
recomputes because the status is part of the input hash.

## Manual corrections

Parser output is immutable. Corrections are stored as profile overrides on top
of it, and a later import merges into the existing overrides rather than
replacing them — a value you corrected by hand is not overwritten. If no manual
`years_experience_total` exists, the parser's inferred value is stored.

The Resume screen's **Profile details** section edits contact fields and
experience rows (employer, title, start, end) and saves them as overrides;
experience titles also feed the title-fit part of the experience dimension.
Skills the parser missed can be added by name (normalized through the
taxonomy), and a per-skill status overrides parser evidence; `not_relevant`
removes a skill from matching. Re-parsing never rewrites these overrides.

## Viewing and comparing versions

The Resume Manager lists every imported version oldest-first. **View** opens a
read-only copy of that version's parsed text; the active version is marked, and
**Make active** switches which version matching uses.

The preview is compared against the version imported *before* it: added lines
are shown on green with a `+`, removed lines on red with a `−`, and unchanged
lines stay neutral. A version's own diff never changes after import because
parser output is immutable. The first version has nothing to compare against,
so its text is shown without highlights.

This compares the extracted text, not the original file — the imported
PDF/DOCX/TXT is stored unchanged and is never edited. **Open original** hands
the stored copy to your default application (visible in the desktop app; the
button is hidden in browser preview where no files exist). The app only opens
paths inside its own resume store.

## Matching

Matching evaluates each job with the `fit.weighted_v1` model and six
dimensions, then aggregates over the dimensions that have data:

```text
fit = Σ w_d · s_d / Σ w_d      (only dimensions with available data)
dimension_coverage = Σ w_d(available) / Σ w_d
```

A missing dimension is excluded, never scored as zero. Each result stores the
effective weights, the per-dimension scores and the coverage.

Weights are editable on the Resume screen. `matching.update_weights` normalizes
them to sum to 1, stores the active set in `scoring_configurations`, and the
next run uses them. Changing weights changes the input hash, so old rows are
replaced rather than shown with the new weights.

| Dimension | Weight | What it does |
|---|---:|---|
| Skills & resume alignment | 0.45 | 80% required coverage + 20% preferred coverage when both exist, with lexical overlap capped at 10%; missing required skills are named |
| Experience & seniority | 0.18 | Seniority ladder distance (50%), stated `N+ years` requirement (30%), title overlap (20%); missing parts are dropped and the rest renormalized |
| Responsibilities alignment | 0.14 | Term overlap between the job description and the resume text; unavailable without both |
| Location & workplace | 0.10 | Remote/hybrid/onsite preference and a parsed country/region eligibility check; a known foreign restriction scores zero |
| Compensation | 0.08 | Posted range against `desired_salary_min` / `desired_salary_target`; neutral when no target is set, unavailable without a salary |
| Freshness | 0.05 | `exp(−ln2 · age_days / 14)` from `posted_at` (falling back to first seen); unavailable without a date |

The fit score is a **heuristic ordering signal, not a probability of an
interview**. Skills carry more weight than before because required coverage is
the part of a posting that cannot be inferred from adjacent prose; the lexical
term overlap contributes at most 10% when requirements exist.

## Requirements and ATS keywords

A posting is analyzed section by section. Skills under **Requirements**,
**Minimum qualifications**, **Must have**, **What you'll need** or **Who you
are** are required. Skills under **Nice to have**, **Preferred**, **Bonus** or
**A plus** are preferred. Responsibility and benefits text is not a requirement.
Inline cues ("must have", "required", "proficient with", and the preferred
cues) override the section. A skill with no section or cue is **mentioned**, not
required, so the app never invents a hard requirement.

The same analysis returns exact ATS keywords with their canonical skill id,
importance, frequency and source section. Job detail shows them grouped
Required / Preferred / Mentioned, and Keyword Insights uses the same importance
for required-keyword prevalence and coverage.

A job with confidently extracted required skills is withheld from Best matches
and Profile matches when coverage is below 50%. The list shows how many were
hidden and offers an opt-in to reveal them. A job with no extracted
requirements is never hidden for missing skills.

## Location eligibility

Remote does not mean worldwide. The analyzer reads structured location forms
("Remote - India", "Remote (US)", "India - Remote") and prose restrictions
("must be located in Africa", "open only to candidates in ...", "authorized to
work in ...", "remote within ..."). A known country or region restriction is a
hard visibility rule for both Find jobs and Profile matches; an unknown
restriction stays unknown and is kept. Profile preferences record a home
location and authorized countries (defaulting to the country of the saved
locations, e.g. Chicago/IL → United States). Relocation or the "show outside my
area" opt-in reveals excluded roles.

## Invalidation and explanations

Each result stores an input hash over the model version, effective weights, the
active resume version's text hash, overrides, skill statuses, skill set, and the
job snapshot (`content_hash`, or a hash of the job fields). A run reuses the
stored result when the hash matches and replaces the previous row for that job
when it does not — so a resume re-import, profile edit, weight change, skill
status change or changed posting all invalidate it.

Per-dimension reason codes and messages are stored in `matching_explanations`
and returned by `matching.get_results`, together with the dimension rows. The Resume screen shows the dimensions, a "limited data" badge under 0.6 coverage,
the explanation lines, and a "How is this calculated?" panel listing the active
weights. Fit is labelled a heuristic score, not a probability of an interview.

## What is stored

Per resume version: the parsed text, sections, experience entries (with split
employer/title), education records, certifications, projects, contact details
and per-skill evidence with spans and tenure (saving skills again replaces the
version's set, so a removed skill does not linger). A re-import records the
previously active version as `parent_version_id`, so version lineage is a chain.

Per profile: contact details, manual overrides, user skill statuses, and the
active scoring configuration. Per matching run: model and version, effective
weights, fit score, dimension coverage, per-dimension scores and reason-level
explanations.

Additional IPC methods: `resume.parse` re-runs the parser on a stored version
without changing it, `resume.set_skill_status` records a status override, and
`keywords.extract` runs taxonomy extraction over arbitrary text.
