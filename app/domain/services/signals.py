"""Signal providers for posting intelligence (spec §9.3).

Each provider is a pure function of ``JobHistory`` + ``SignalContext``: no I/O,
no clock of its own, no exceptions for absent data. When the evidence a
provider needs is not there it returns ``missing`` with a reason — never a
low-risk number — because absent data is not a sign of health (§9.1 rule 2).

The formulas are the documented initial heuristics of ``risk.heuristic_v1``.
They are intentionally simple and explainable; no ML is involved.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from statistics import median
from typing import ClassVar

from app.domain.models.common import Evidence
from app.domain.models.enums import SignalStatus
from app.domain.models.posting import JobHistory, SignalContext, SignalResult

PROVIDER_VERSION = "1.0.0"

TRANSIENT_ENDPOINT_STATES: frozenset[str] = frozenset({"timeout", "error"})
#: A block or a login wall says something about *us*, not about the posting.
NON_EVIDENCE_ENDPOINT_STATES: frozenset[str] = frozenset({"blocked", "auth_required"})
ENDPOINT_CONTRIBUTIONS: dict[str, float] = {
    "ok": 0.0,
    "form_unavailable": 0.70,
    "generic_redirect": 0.60,
    "closed_message": 0.95,
    "gone_404_410": 0.90,
}
ENDPOINT_CONFIDENCE: dict[str, float] = {
    "ok": 0.60,
    "closed_message": 0.90,
    "gone_404_410": 0.85,
    "form_unavailable": 0.60,
    "generic_redirect": 0.50,
}
STRONG_ENDPOINT_STATES: frozenset[str] = frozenset({"closed_message", "gone_404_410"})

CLOSURE_PATTERNS: tuple[str, ...] = (
    "no longer accepting applications",
    "no longer accepting",
    "position has been filled",
    "role has been filled",
    "this job has expired",
    "this posting has expired",
    "applications are closed",
    "applications have closed",
    "this posting is closed",
    "job posting has closed",
    "bewerbungen sind nicht mehr möglich",
    "stelle wurde besetzt",
    "anzeige ist abgelaufen",
    "n'accepte plus les candidatures",
    "poste a été pourvu",
    "offre a expiré",
    "ya no acepta solicitudes",
    "puesto ha sido cubierto",
    "oferta ha expirado",
)
NEGATION_MARKERS: tuple[str, ...] = ("not ", "never ", "n't ", "kein", "keine", "nicht ")

BOILERPLATE_PHRASES: tuple[str, ...] = (
    "fast-paced environment",
    "fast paced environment",
    "team player",
    "competitive salary",
    "self-starter",
    "wear many hats",
    "dynamic environment",
    "detail-oriented",
    "exciting opportunity",
    "other duties as assigned",
)

#: First public year of widely used technologies, for the implausibility check.
TECH_FIRST_YEAR: dict[str, int] = {
    "go": 2009,
    "typescript": 2012,
    "react": 2013,
    "docker": 2013,
    "kubernetes": 2014,
    "swift": 2014,
    "terraform": 2014,
    "vue": 2014,
    "graphql": 2015,
    "rust": 2015,
    "kotlin": 2016,
    "svelte": 2016,
}
ENTRY_LEVEL_MARKERS: tuple[str, ...] = (
    "entry level",
    "entry-level",
    "junior",
    "graduate",
    "internship",
    "trainee",
)
_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.IGNORECASE)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _days_between(start: datetime, end: datetime) -> float:
    return max(0.0, (end - start).total_seconds() / 86_400)


def _fallback_age_risk(age_days: float) -> float:
    """Absolute age curve used when no comparable-role baseline exists."""
    if age_days < 30:
        return 0.0
    if age_days < 90:
        return 0.30 * (age_days - 30) / 60
    if age_days < 180:
        return 0.30 + 0.20 * (age_days - 90) / 90
    return 0.50


class _Provider:
    """Shared plumbing: identity, missing results, and a cautious voice."""

    id: ClassVar[str] = ""
    version: ClassVar[str] = PROVIDER_VERSION
    description: ClassVar[str] = ""
    default_weight: ClassVar[float] = 0.0
    evidence_source: ClassVar[str] = "observations"

    def missing(self, reason: str, now: datetime | None) -> SignalResult:
        return SignalResult(
            signal_id=self.id,
            provider_version=self.version,
            status=SignalStatus.MISSING,
            risk_contribution=None,
            confidence=0.0,
            missing_reason=reason,
            explanation=f"Not assessed: {reason}.",
            description=self.description,
            evidence_source=self.evidence_source,
            evaluated_at=now,
        )

    def result(
        self,
        *,
        risk: float,
        confidence: float,
        explanation: str,
        evidence: list[Evidence],
        now: datetime,
        observed_at: datetime | None,
        strong: bool = False,
    ) -> SignalResult:
        return SignalResult(
            signal_id=self.id,
            provider_version=self.version,
            status=SignalStatus.AVAILABLE,
            risk_contribution=_clamp(risk, 0.0, 1.0),
            confidence=_clamp(confidence, 0.0, 1.0),
            strong=strong,
            evidence=tuple(evidence),
            explanation=explanation,
            description=self.description,
            evidence_source=self.evidence_source,
            observed_at=observed_at,
            evaluated_at=now,
        )

    def evaluate(self, history: JobHistory, ctx: SignalContext) -> SignalResult:
        raise NotImplementedError


class PostingAgeSignal(_Provider):
    """Time since first observation, relative to comparable roles."""

    id = "signal.posting_age"
    description = "How long this posting has been visible, compared with similar roles."
    default_weight = 0.10
    evidence_source = "jobs.first_seen_at"

    def evaluate(self, history: JobHistory, ctx: SignalContext) -> SignalResult:
        now = ctx.now or history.last_seen_at
        if now is None or history.first_seen_at is None:
            return self.missing("the posting has no first-seen date", now)

        age_days = _days_between(history.first_seen_at, now)
        baseline, scope = self._baseline(history, ctx)
        if baseline is not None and baseline > 0:
            ratio = age_days / baseline
            risk = _clamp((ratio - 1.5) / 4.5, 0.0, 1.0)
            confidence = 0.70 if scope == "cohort" else 0.55
        else:
            risk = _fallback_age_risk(age_days)
            confidence = 0.35
            scope = "absolute"

        # Age alone never implies high risk (§9.3, capped at 0.5).
        risk = min(risk, 0.5)
        confidence *= min(1.0, history.observed_days(now) / 14)

        baseline_note = (
            f" against a median of {baseline:.0f} days for comparable postings"
            if baseline
            else " with no comparable-role baseline available"
        )
        return self.result(
            risk=risk,
            confidence=confidence,
            explanation=(
                f"First observed {age_days:.0f} days ago{baseline_note}. "
                "Posting age alone is weak evidence."
            ),
            evidence=[
                Evidence(
                    kind="posting_age",
                    description=f"First seen {age_days:.1f} days ago",
                    data={
                        "age_days": round(age_days, 1),
                        "baseline_days": round(baseline, 1) if baseline else None,
                        "baseline_source": scope,
                    },
                    observed_at=history.first_seen_at,
                )
            ],
            now=now,
            observed_at=history.last_seen_at,
        )

    def _baseline(
        self, history: JobHistory, ctx: SignalContext
    ) -> tuple[float | None, str]:
        cohort = history.cohort
        if cohort and cohort.sample_size >= ctx.cohort_min_sample and cohort.median_days:
            return float(cohort.median_days), "cohort"
        company = history.company_summary
        if (
            company
            and company.n_unique >= ctx.company_min_sample
            and company.median_duration_days
        ):
            return float(company.median_duration_days), "company"
        return None, "absolute"


class RepostFrequencySignal(_Provider):
    """Reposts, date resets and identity churn in the last year."""

    id = "signal.repost_frequency"
    description = "How often this position has been re-advertised."
    default_weight = 0.20
    evidence_source = "repost_events"

    def evaluate(self, history: JobHistory, ctx: SignalContext) -> SignalResult:
        now = ctx.now or history.last_seen_at
        if now is None or history.first_seen_at is None:
            return self.missing("the posting has no observation window yet", now)
        if not history.observed_crawl_cycles:
            return self.missing("no full crawl cycle has been observed yet", now)

        cutoff = now - timedelta(days=365)
        recent = [point for point in history.reposts if point.at >= cutoff]
        n_identical = sum(1 for p in recent if p.kind == "reposted_identical")
        n_reset = sum(1 for p in recent if p.kind == "posted_date_reset")
        n_identity = sum(1 for p in recent if p.kind == "id_change_only")

        risk = min(1.0, 0.20 * n_identical + 0.10 * n_reset + 0.10 * n_identity)
        identical_at = sorted(p.at for p in recent if p.kind == "reposted_identical")
        interval_days: float | None = None
        if len(identical_at) >= 2:
            gaps = [
                _days_between(identical_at[i], identical_at[i + 1])
                for i in range(len(identical_at) - 1)
            ]
            interval_days = median(gaps)
            if interval_days < 45:
                risk = min(1.0, risk + 0.15)

        observed = history.observed_days(now)
        confidence = max(0.15, min(1.0, observed / 90) * 0.8)
        interval_note = (
            f" The median gap between identical reposts is {interval_days:.0f} days."
            if interval_days is not None
            else ""
        )
        return self.result(
            risk=risk,
            confidence=confidence,
            explanation=(
                f"{len(recent)} repost event(s) in the last year for this position."
                f"{interval_note}"
            ),
            evidence=[
                Evidence(
                    kind="repost_events",
                    description="Reposts recorded in the last 365 days",
                    data={
                        "reposted_identical": n_identical,
                        "posted_date_reset": n_reset,
                        "id_change_only": n_identity,
                        "median_interval_days": (
                            round(interval_days, 1) if interval_days is not None else None
                        ),
                    },
                    observed_at=recent[-1].at if recent else history.first_seen_at,
                )
            ],
            now=now,
            observed_at=history.last_seen_at,
        )


class EndpointAvailabilitySignal(_Provider):
    """What the employer's own application page says (§9.3)."""

    id = "signal.endpoint_availability"
    description = "Whether the application page is reachable and still accepting."
    default_weight = 0.25
    evidence_source = "application_endpoint_observations"

    def evaluate(self, history: JobHistory, ctx: SignalContext) -> SignalResult:
        now = ctx.now or history.last_seen_at
        if now is None:
            return self.missing("no current time is available", None)

        max_age = max(1, ctx.endpoint_obs_max_age_days)
        cutoff = now - timedelta(days=max_age)
        recent = sorted(
            (point for point in history.endpoints if point.at >= cutoff),
            key=lambda point: point.at,
        )
        if not recent:
            return self.missing(
                f"no application endpoint check in the last {max_age} days", now
            )

        latest = recent[-1]
        if latest.state in NON_EVIDENCE_ENDPOINT_STATES:
            return self.missing(
                f"the last check was {latest.state}, which says nothing about the posting",
                now,
            )

        age_days = _days_between(latest.at, now)
        decay = 0.5 + 0.5 * (1 - min(1.0, age_days / max_age))

        if latest.state in TRANSIENT_ENDPOINT_STATES:
            consecutive = 0
            for point in reversed(recent):
                if point.state not in TRANSIENT_ENDPOINT_STATES:
                    break
                consecutive += 1
            if consecutive < max(1, ctx.transient_failures_required):
                return self.missing(
                    "a single failed check is transient, not evidence", now
                )
            risk, confidence, strong = 0.40, 0.40 * decay, False
            note = f"{consecutive} consecutive failed checks"
        elif latest.state in ENDPOINT_CONTRIBUTIONS:
            risk = ENDPOINT_CONTRIBUTIONS[latest.state]
            confidence = ENDPOINT_CONFIDENCE[latest.state] * decay
            strong = latest.state in STRONG_ENDPOINT_STATES
            note = f"the application page reported {latest.state}"
        else:
            return self.missing(
                f"the endpoint state {latest.state!r} is not recognised", now
            )

        return self.result(
            risk=risk,
            confidence=confidence,
            explanation=f"Last endpoint check: {note}.",
            evidence=[
                Evidence(
                    kind="endpoint_state",
                    description=f"Endpoint state {latest.state}",
                    data={
                        "state": latest.state,
                        "http_status": latest.http_status,
                        "final_url": latest.final_url,
                        "age_days": round(age_days, 1),
                    },
                    observed_at=latest.at,
                    source_ref=latest.final_url,
                )
            ],
            now=now,
            observed_at=latest.at,
            strong=strong,
        )


class ContentStalenessSignal(_Provider):
    """Unchanged text, copied text, generic text, missing specifics."""

    id = "signal.content_staleness"
    description = "Whether the posting text has changed or describes nothing specific."
    default_weight = 0.10
    evidence_source = "job_snapshots"

    def evaluate(self, history: JobHistory, ctx: SignalContext) -> SignalResult:
        now = ctx.now or history.last_seen_at
        if now is None:
            return self.missing("no current time is available", None)
        observed = history.observed_days(now)
        if observed < 14 and not history.snapshots:
            return self.missing(
                "the posting has not been observed long enough to compare text", now
            )

        unchanged = self._unchanged_factor(history)
        copy = self._copy_factor(history)
        generic = self._generic_factor(history)
        missing_specifics = 0.0 if history.requirements_text.strip() else 1.0

        risk = 0.35 * unchanged + 0.25 * copy + 0.25 * generic + 0.15 * missing_specifics
        confidence = 0.45 if len(history.snapshots) >= 2 else 0.30
        specifics = (
            "no requirements section was found"
            if missing_specifics
            else "a requirements section is present"
        )
        return self.result(
            risk=risk,
            confidence=confidence,
            explanation=(
                f"Text analysis: unchanged share {unchanged:.2f}, copied share "
                f"{copy:.2f}, generic phrasing {generic:.2f}; {specifics}."
            ),
            evidence=[
                Evidence(
                    kind="content_staleness",
                    description="Contributions to the content staleness factor",
                    data={
                        "unchanged": round(unchanged, 3),
                        "copy": round(copy, 3),
                        "generic": round(generic, 3),
                        "missing_specifics": missing_specifics,
                        "snapshots": len(history.snapshots),
                    },
                    observed_at=history.snapshots[-1].at
                    if history.snapshots
                    else (history.first_seen_at or now),
                )
            ],
            now=now,
            observed_at=history.snapshots[-1].at if history.snapshots else None,
        )

    def _unchanged_factor(self, history: JobHistory) -> float:
        if len(history.snapshots) < 3:
            return 0.0
        ordered = sorted(history.snapshots, key=lambda point: point.at)
        newest = ordered[-1]
        last_change = ordered[0].at
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current.content_hash != previous.content_hash:
                last_change = current.at
        days_unchanged = _days_between(last_change, newest.at)
        return _clamp((days_unchanged - 60) / 180, 0.0, 1.0)

    def _copy_factor(self, history: JobHistory) -> float:
        if len(history.related_jobs) < 5:
            return 0.0
        copied = sum(
            1 for related in history.related_jobs if related.description_similarity >= 0.9
        )
        return _clamp(copied / len(history.related_jobs), 0.0, 1.0)

    def _generic_factor(self, history: JobHistory) -> float:
        text = (history.description_text or history.page_text).casefold()
        if not text.strip():
            return 0.0
        hits = sum(text.count(phrase) for phrase in BOILERPLATE_PHRASES)
        words = max(1, len(text.split()))
        return _clamp(hits / max(1.0, words / 120), 0.0, 1.0)


class DuplicateConsistencySignal(_Provider):
    """Whether the sources that carry this posting agree with each other."""

    id = "signal.duplicate_consistency"
    description = "Whether independent sources describe this posting the same way."
    default_weight = 0.10
    evidence_source = "job_source_aliases"

    def evaluate(self, history: JobHistory, ctx: SignalContext) -> SignalResult:
        now = ctx.now or history.last_seen_at
        if now is None:
            return self.missing("no current time is available", None)
        statuses = {
            source: status
            for source, status in history.statuses_by_source.items()
            if status
        }
        if len(statuses) < 2:
            return self.missing("only one source is known for this posting", now)

        closed = sorted(s for s, status in statuses.items() if status in {"closed", "removed"})
        active = sorted(s for s, status in statuses.items() if status == "active")
        if closed and active:
            risk, confidence = 0.8, 0.8
            explanation = (
                "One source lists this posting as closed while another lists it as active."
            )
        else:
            risk = min(0.6, 0.15 * max(0, history.field_conflicts))
            confidence = 0.5
            explanation = (
                f"{history.field_conflicts} contradictory field(s) across "
                f"{len(statuses)} sources."
            )

        return self.result(
            risk=risk,
            confidence=confidence,
            explanation=explanation,
            evidence=[
                Evidence(
                    kind="source_statuses",
                    description="Status reported by each source",
                    data={"statuses": statuses, "field_conflicts": history.field_conflicts},
                    observed_at=now,
                )
            ],
            now=now,
            observed_at=history.last_seen_at,
        )


class CompanyHiringActivitySignal(_Provider):
    """The employer's recent posting behaviour, when the sample allows it."""

    id = "signal.company_hiring_activity"
    description = "How this employer's recent postings behave (reposts, inactivity)."
    default_weight = 0.15
    evidence_source = "company_hiring_summaries"

    def evaluate(self, history: JobHistory, ctx: SignalContext) -> SignalResult:
        now = ctx.now or history.last_seen_at
        if now is None:
            return self.missing("no current time is available", None)
        summary = history.company_summary
        if summary is None:
            return self.missing("no company hiring summary is available", now)
        if summary.sample_tier != "adequate":
            return self.missing(
                f"the company sample is {summary.sample_tier} for a company-level view", now
            )

        repost_part = 0.5 * min(1.0, summary.repost_ratio / 0.30)
        inactive_part = 0.3 * min(1.0, summary.inactive_ratio / 0.40)
        risk = min(0.7, repost_part + inactive_part)
        confidence = min(0.6, 0.15 + summary.n_unique / 60)

        return self.result(
            risk=risk,
            confidence=confidence,
            explanation=(
                f"Across {summary.n_unique} recent postings this employer re-advertised "
                f"{summary.repost_ratio:.0%} and had {summary.inactive_ratio:.0%} "
                "classified as inactive or stale."
            ),
            evidence=[
                Evidence(
                    kind="company_summary",
                    description="Company hiring metrics used",
                    data={
                        "n_unique": summary.n_unique,
                        "window_days": round(summary.window_days, 1),
                        "repost_ratio": round(summary.repost_ratio, 3),
                        "inactive_ratio": round(summary.inactive_ratio, 3),
                    },
                    observed_at=now,
                )
            ],
            now=now,
            observed_at=now,
        )


class ExplicitClosureSignal(_Provider):
    """Closure wording on the page itself (§9.3, strong evidence)."""

    id = "signal.explicit_closure_language"
    description = "Whether the page says the role is closed or filled."
    default_weight = 0.30
    evidence_source = "job_snapshots.text"

    def evaluate(self, history: JobHistory, ctx: SignalContext) -> SignalResult:
        now = ctx.now or history.last_seen_at
        if now is None:
            return self.missing("no current time is available", None)
        text = (history.page_text or history.description_text).strip()
        if not text:
            return self.missing("no page text has been stored for this posting", now)

        folded = text.casefold()
        for pattern in CLOSURE_PATTERNS:
            index = folded.find(pattern)
            if index < 0 or self._negated(folded, index, pattern):
                continue
            excerpt = text[max(0, index - 40) : index + len(pattern) + 40]
            return self.result(
                risk=0.95,
                confidence=0.90,
                explanation=f'The page contains closure wording: "{pattern}".',
                evidence=[
                    Evidence(
                        kind="closure_language",
                        description="Closure phrase found in the page text",
                        data={"pattern": pattern, "excerpt": excerpt},
                        observed_at=now,
                    )
                ],
                now=now,
                observed_at=history.last_seen_at,
                strong=True,
            )

        return self.result(
            risk=0.0,
            confidence=0.25,
            explanation="The page text was inspected and contains no closure wording.",
            evidence=[
                Evidence(
                    kind="closure_language",
                    description="No closure phrase found in the inspected page text",
                    data={"characters_inspected": len(text)},
                    observed_at=now,
                )
            ],
            now=now,
            observed_at=history.last_seen_at,
        )

    @staticmethod
    def _negated(folded: str, index: int, pattern: str) -> bool:
        """Catch "applications are *not* closed" and its translations.

        The marker usually sits inside the match rather than before it, so the
        window spans the phrase and a little of what precedes it.
        """
        if pattern.startswith("no longer"):
            return False
        window = folded[max(0, index - 24) : index + len(pattern)]
        return any(marker in window for marker in NEGATION_MARKERS)


class ImplausibleRequirementsSignal(_Provider):
    """Requirements that cannot be true (technology older than itself, etc.)."""

    id = "signal.implausible_requirements"
    description = "Whether the requirements contradict themselves."
    default_weight = 0.05
    evidence_source = "job_snapshots.requirements"

    def evaluate(self, history: JobHistory, ctx: SignalContext) -> SignalResult:
        now = ctx.now or history.last_seen_at
        if now is None:
            return self.missing("no current time is available", None)
        text = (history.requirements_text or history.description_text).strip()
        if not text:
            return self.missing("no requirements text has been parsed", now)

        contradictions = self._technology_contradictions(text, now)
        contradictions.extend(self._entry_level_contradictions(text))
        risk = min(0.8, 0.6 * len(contradictions))
        explanation = (
            f"{len(contradictions)} implausible requirement(s) found."
            if contradictions
            else "The requirements contain no obvious contradictions."
        )
        return self.result(
            risk=risk,
            confidence=0.35,
            explanation=explanation,
            evidence=[
                Evidence(
                    kind="implausible_requirements",
                    description="Requirement contradictions detected",
                    data={"contradictions": contradictions},
                    observed_at=now,
                )
            ],
            now=now,
            observed_at=history.last_seen_at,
        )

    @staticmethod
    def _technology_contradictions(text: str, now: datetime) -> list[str]:
        found: list[str] = []
        folded = text.casefold()
        for tech, first_year in TECH_FIRST_YEAR.items():
            age_years = max(0, now.year - first_year)
            for match in _YEARS.finditer(folded):
                window = folded[match.end() : match.end() + 80]
                if not re.search(rf"\b{re.escape(tech)}\b", window):
                    continue
                years = int(match.group(1))
                if years > age_years + 0.5:
                    found.append(
                        f"{years} years of {tech} exceeds the {age_years} years it has existed"
                    )
        return found

    @staticmethod
    def _entry_level_contradictions(text: str) -> list[str]:
        folded = text.casefold()
        if not any(marker in folded for marker in ENTRY_LEVEL_MARKERS):
            return []
        return [
            f"entry-level role asking for {match.group(1)} years"
            for match in _YEARS.finditer(folded)
            if int(match.group(1)) >= 8
        ]


#: Every provider registered under ``risk.heuristic_v1`` (spec §9.3).
DEFAULT_SIGNAL_PROVIDERS: tuple[_Provider, ...] = (
    PostingAgeSignal(),
    RepostFrequencySignal(),
    EndpointAvailabilitySignal(),
    ContentStalenessSignal(),
    DuplicateConsistencySignal(),
    CompanyHiringActivitySignal(),
    ExplicitClosureSignal(),
    ImplausibleRequirementsSignal(),
)
