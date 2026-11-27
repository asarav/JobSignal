"""Matching strategies and scoring model (spec sec 11)."""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from app.domain.models.common import Explanation, Reason
from app.domain.models.matching import DimensionScore, MatchContext, MatchScore, ScoringConfig
from app.domain.ports.matching import MatchingStrategy, ScoringModel

DIMENSIONS = [
    "skills_resume",
    "experience_seniority",
    "responsibilities",
    "location_workplace",
    "compensation",
    "freshness",
]
# Spec sec 10.3/11.3: how strongly each resume evidence level supports a skill.
EVIDENCE_STRENGTH = {
    "explicit_with_experience": 1.0,
    "explicit": 0.8,
    "inferred": 0.5,
    "limited": 0.3,
}
#: Spec sec 11.3: user status overrides evidence.  An explicit expert-level
#: answer scores above a plain "have experience", so the extra option changes
#: coverage rather than being cosmetic; existing have-experience answers are
#: rescaled from 1.0 to 0.9.
SKILL_STATUS_STRENGTH = {
    "extensive_experience": 1.0,
    "have_experience": 0.9,
    "learning": 0.4,
    "no_experience": 0.0,
    "not_relevant": None,
}
DEFAULT_WEIGHTS = {
    "skills_resume": 0.35,
    "experience_seniority": 0.20,
    "responsibilities": 0.20,
    "location_workplace": 0.10,
    "compensation": 0.10,
    "freshness": 0.05,
}
SENIORITY_RANK = {
    "intern": 0,
    "junior": 1,
    "entry": 1,
    "mid": 2,
    "intermediate": 2,
    "senior": 3,
    "lead": 4,
    "staff": 5,
    "principal": 5,
    "director": 6,
    "executive": 6,
}
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+#.]*")
_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:years|yrs)", re.IGNORECASE)
_STOPWORDS = frozenset(
    "the and for with you your our are will that this from have has can all any job role team work".split()
)


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _tokens(text: object) -> set[str]:
    if not isinstance(text, str):
        return set()
    return {
        token
        for token in _WORD_RE.findall(text.lower())
        if len(token) >= 2 and token not in _STOPWORDS
    }


def _overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _dimension(
    strategy: MatchingStrategy,
    *,
    score: float | None,
    available: bool,
    summary: str,
    reasons: Sequence[Reason],
    weight: float,
) -> DimensionScore:
    return DimensionScore(
        dimension=strategy.dimension,
        strategy_id=strategy.id,
        strategy_version=strategy.version,
        score=max(0.0, min(1.0, score)) if score is not None else None,
        available=available,
        weight=weight,
        explanation=Explanation(summary=summary, reasons=list(reasons)),
    )


def normalize_weights(weights: Mapping[str, object]) -> dict[str, float]:
    """Normalize user weights over the six dimensions, falling back per key."""
    merged = dict(DEFAULT_WEIGHTS)
    for dimension in DIMENSIONS:
        raw = weights.get(dimension)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw >= 0:
            merged[dimension] = float(raw)
    total = sum(merged.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {dimension: merged[dimension] / total for dimension in DIMENSIONS}


class SkillsResumeStrategy(MatchingStrategy):
    id = "match.skills_resume"
    version = "1.2.0"
    dimension = "skills_resume"

    def score(  # type: ignore[override]
        self, profile: Any, job: Mapping[str, Any], ctx: MatchContext
    ) -> DimensionScore:
        cand_skills = set(getattr(profile, "skills", []) or [])
        evidence = getattr(profile, "skill_evidence", None) or {}
        statuses = getattr(profile, "skill_status", None) or {}
        req = _as_list(job.get("required_skills"))
        pref = _as_list(job.get("preferred_skills"))
        excluded = {skill for skill in req + pref if statuses.get(skill) == "not_relevant"}
        req = [skill for skill in req if skill not in excluded]
        pref = [skill for skill in pref if skill not in excluded]

        def strength(skill: object) -> float:
            status = statuses.get(str(skill)) if isinstance(statuses, Mapping) else None
            if status in SKILL_STATUS_STRENGTH:
                override = SKILL_STATUS_STRENGTH[status]
                return float(override) if override is not None else 0.0
            if skill not in cand_skills:
                return 0.0
            if isinstance(evidence, Mapping) and skill in evidence:
                return EVIDENCE_STRENGTH.get(str(evidence[skill]), 0.8)
            return 1.0

        def coverage(skills: list[object]) -> float:
            if not skills:
                return 1.0
            return sum(strength(skill) for skill in skills) / len(skills)

        cov_req = coverage(req)
        cov_pref = coverage(pref)
        lexical = _overlap(
            _tokens(getattr(profile, "resume_text", "")),
            _tokens(job.get("description_text")),
        )
        if req and pref:
            score = 0.70 * (0.80 * cov_req + 0.20 * cov_pref) + 0.30 * lexical
        elif req:
            score = 0.70 * cov_req + 0.30 * lexical
        elif pref:
            score = 0.70 * cov_pref + 0.30 * lexical
        else:
            score = lexical
        available = bool(req or pref or lexical)
        if not available:
            return _dimension(
                self,
                score=None,
                available=False,
                summary="no skills or text to compare",
                reasons=[Reason(code="skills.unavailable", message="no skill or text data")],
                weight=0.35,
            )
        missing = [skill for skill in req if strength(skill) == 0 and skill not in excluded]
        reasons: list[Reason] = []
        if missing:
            shown = ", ".join(str(skill) for skill in missing[:6])
            reasons.append(Reason(code="skills.missing", message=f"missing required: {shown}"))
        else:
            reasons.append(Reason(code="skills.ok", message="all required skills covered"))
        if excluded:
            reasons.append(
                Reason(
                    code="skills.excluded",
                    message=f"{len(excluded)} skill(s) marked not relevant were ignored",
                )
            )
        status_overrides = [skill for skill in req + pref if str(skill) in statuses]
        if status_overrides:
            reasons.append(
                Reason(
                    code="skills.user_status",
                    message=f"{len(status_overrides)} skill status override(s) applied",
                )
            )
        reasons.append(
            Reason(code="skills.lexical", message=f"resume/job text overlap {lexical:.0%}")
        )
        return _dimension(
            self,
            score=score,
            available=True,
            summary="skills coverage and text overlap",
            reasons=reasons,
            weight=0.35,
        )


class ExperienceSeniorityStrategy(MatchingStrategy):
    id = "match.experience_seniority"
    version = "1.1.0"
    dimension = "experience_seniority"

    def score(  # type: ignore[override]
        self, profile: Any, job: Mapping[str, Any], ctx: MatchContext
    ) -> DimensionScore:
        years = getattr(profile, "years_experience_total", None)
        preferences = getattr(profile, "preferences", None) or {}
        candidate_rank = SENIORITY_RANK.get(str(preferences.get("seniority", "")).lower())
        if candidate_rank is None and isinstance(years, (int, float)):
            candidate_rank = 1 if years < 2 else 2 if years < 5 else 3 if years < 9 else 4
        job_rank = SENIORITY_RANK.get(str(job.get("seniority", "")).lower())

        parts: list[tuple[float, float]] = []  # (score, part weight)
        reasons: list[Reason] = []
        if candidate_rank is not None and job_rank is not None:
            distance = abs(job_rank - candidate_rank)
            if distance == 0:
                seniority = 1.0
            elif distance == 1:
                seniority = 0.55 if job_rank > candidate_rank else 0.7
            elif distance == 2:
                seniority = 0.3
            else:
                seniority = 0.1
            parts.append((seniority, 0.5))
            reasons.append(
                Reason(
                    code="experience.seniority",
                    message=f"job seniority is {distance} step(s) away",
                )
            )

        stated = _YEARS_RE.search(str(job.get("description_text") or ""))
        if isinstance(years, (int, float)) and stated:
            minimum = int(stated.group(1))
            if minimum > 0:
                years_fit = 1.0 if years >= minimum else max(0.0, years / minimum) ** 1.5
                parts.append((years_fit, 0.3))
                reasons.append(
                    Reason(
                        code="experience.years",
                        message=f"{years:g} years against {minimum}+ requested",
                    )
                )

        candidate_titles = {str(title) for title in _as_list(getattr(profile, "titles", []))}
        title_fit = _overlap(_tokens(job.get("title")), set().union(
            *(_tokens(title) for title in candidate_titles)
        ))
        if candidate_titles:
            parts.append((title_fit, 0.2))
            reasons.append(
                Reason(code="experience.title", message=f"title overlap {title_fit:.0%}")
            )

        if not parts:
            return _dimension(
                self,
                score=None,
                available=False,
                summary="no experience data",
                reasons=[Reason(code="experience.unavailable", message="no stated experience")],
                weight=0.20,
            )
        score = sum(value * weight for value, weight in parts) / sum(
            weight for _value, weight in parts
        )
        return _dimension(
            self,
            score=score,
            available=True,
            summary="seniority, years and title fit",
            reasons=reasons,
            weight=0.20,
        )


class ResponsibilitiesStrategy(MatchingStrategy):
    id = "match.responsibilities"
    version = "1.1.0"
    dimension = "responsibilities"

    def score(  # type: ignore[override]
        self, profile: Any, job: Mapping[str, Any], ctx: MatchContext
    ) -> DimensionScore:
        job_tokens = _tokens(job.get("description_text"))
        resume_tokens = _tokens(getattr(profile, "resume_text", ""))
        available = bool(job_tokens and resume_tokens)
        if not available:
            return _dimension(
                self,
                score=None,
                available=False,
                summary="no responsibilities text",
                reasons=[Reason(code="responsibilities.unavailable", message="missing text")],
                weight=0.20,
            )
        score = _overlap(job_tokens, resume_tokens)
        return _dimension(
            self,
            score=score,
            available=True,
            summary="job text against resume text",
            reasons=[
                Reason(
                    code="responsibilities.overlap",
                    message=f"{score:.0%} of job terms appear in the resume",
                )
            ],
            weight=0.20,
        )


class LocationWorkplaceStrategy(MatchingStrategy):
    id = "match.location_workplace"
    version = "1.1.0"
    dimension = "location_workplace"

    def score(  # type: ignore[override]
        self, profile: Any, job: Mapping[str, Any], ctx: MatchContext
    ) -> DimensionScore:
        workplace = str(job.get("workplace_type") or "unknown").lower()
        location_text = _location_text(job.get("locations"))
        preferences = getattr(profile, "preferences", None) or {}
        wanted_types = {
            str(value).lower() for value in _as_list(preferences.get("workplace_types"))
        }
        remote_ok = bool(preferences.get("remote_ok")) or "remote" in wanted_types
        wanted_locations = [str(value).lower() for value in _as_list(preferences.get("locations"))]
        if workplace == "unknown" and not location_text:
            return _dimension(
                self,
                score=None,
                available=False,
                summary="no location data",
                reasons=[Reason(code="location.unavailable", message="job location unknown")],
                weight=0.10,
            )

        reasons: list[Reason] = []
        workplace_score = 0.7
        if workplace == "remote":
            workplace_score = 1.0 if remote_ok else 0.2
        elif wanted_types:
            workplace_score = 1.0 if workplace in wanted_types else 0.3
        reasons.append(
            Reason(
                code="location.workplace",
                message=f"job is {workplace}; preference {', '.join(sorted(wanted_types)) or 'unset'}",
            )
        )

        location_score = 0.7
        if wanted_locations and location_text:
            matched = any(item in location_text or location_text in item for item in wanted_locations)
            location_score = 1.0 if matched else (0.5 if preferences.get("relocation") else 0.2)
            reasons.append(
                Reason(
                    code="location.match",
                    message=("location matches" if matched else "location differs from preference"),
                )
            )
        elif not wanted_locations:
            reasons.append(
                Reason(code="location.open", message="no location preference; neutral score")
            )

        score = 0.6 * workplace_score + 0.4 * location_score
        return _dimension(
            self,
            score=score,
            available=True,
            summary="workplace and location preferences",
            reasons=reasons,
            weight=0.10,
        )


class CompensationStrategy(MatchingStrategy):
    id = "match.compensation"
    version = "1.1.0"
    dimension = "compensation"

    def score(  # type: ignore[override]
        self, profile: Any, job: Mapping[str, Any], ctx: MatchContext
    ) -> DimensionScore:
        low, high = _salary_bounds(job.get("salary"))
        if low is None and high is None:
            return _dimension(
                self,
                score=None,
                available=False,
                summary="no salary data",
                reasons=[Reason(code="compensation.unavailable", message="salary not listed")],
                weight=0.10,
            )
        low = low if low is not None else high
        high = high if high is not None else low
        assert low is not None and high is not None
        preferences = getattr(profile, "preferences", None) or {}
        target = _number(preferences.get("desired_salary_target"))
        minimum = _number(preferences.get("desired_salary_min"))
        if target is None and minimum is None:
            return _dimension(
                self,
                score=0.6,
                available=True,
                summary="salary listed, no target",
                reasons=[Reason(code="compensation.no_target", message="no salary target set")],
                weight=0.10,
            )
        target = target if target is not None else minimum
        minimum = minimum if minimum is not None else target
        assert target is not None and minimum is not None
        if high >= target:
            score = 1.0
        elif high >= minimum:
            span = max(1.0, target - minimum)
            score = 0.6 + 0.4 * (high - minimum) / span
        else:
            score = max(0.0, 0.6 * (high / max(1.0, minimum)))
        return _dimension(
            self,
            score=score,
            available=True,
            summary="salary against target",
            reasons=[
                Reason(
                    code="compensation.fit",
                    message=f"posted {low:g}–{high:g}; target {target:g}",
                )
            ],
            weight=0.10,
        )


class FreshnessStrategy(MatchingStrategy):
    id = "match.freshness"
    version = "1.1.0"
    dimension = "freshness"

    def score(  # type: ignore[override]
        self, profile: Any, job: Mapping[str, Any], ctx: MatchContext
    ) -> DimensionScore:
        value = job.get("posted_at") or job.get("first_seen_at") or job.get("updated_at_hint")
        when = _parse_datetime(value)
        if when is None:
            return _dimension(
                self,
                score=None,
                available=False,
                summary="no posting date",
                reasons=[Reason(code="freshness.unavailable", message="posting date unknown")],
                weight=0.05,
            )
        now = ctx.now or datetime.now(UTC)
        age_days = max(0.0, (now - when).total_seconds() / 86400)
        score = math.exp(-math.log(2) * age_days / 14)
        return _dimension(
            self,
            score=score,
            available=True,
            summary="posting age",
            reasons=[
                Reason(code="freshness.age", message=f"posted {age_days:.0f} day(s) ago")
            ],
            weight=0.05,
        )


class WeightedScoringModel(ScoringModel):
    id = "fit.weighted_v1"
    version = "1.1.0"

    def evaluate(  # type: ignore[override]
        self,
        profile: Any,
        job: Mapping[str, Any],
        prefs: Any,
        strategies: Sequence[MatchingStrategy],
        cfg: ScoringConfig,
    ) -> MatchScore:
        weights = normalize_weights(cfg.weights)
        now = datetime.now(UTC)
        dim_scores = [
            strategy.score(profile, job, MatchContext(now=now))  # type: ignore[arg-type]
            for strategy in strategies
        ]
        numerator = 0.0
        denominator = 0.0
        for dimension in dim_scores:
            weight = weights.get(dimension.dimension, dimension.weight)
            if dimension.available and dimension.score is not None:
                numerator += weight * dimension.score
                denominator += weight
        fit = numerator / denominator if denominator > 0 else 0.0
        coverage = denominator / sum(weights.values()) if weights else 0.0
        return MatchScore(
            model_id=self.id,
            model_version=self.version,
            fit_score=max(0.0, min(1.0, fit)),
            dimension_coverage=max(0.0, min(1.0, coverage)),
            dimensions=dim_scores,
            explanation=Explanation(summary="fit score (heuristic)", reasons=[]),
            weights=weights,
            evaluated_at=now,
        )


def _location_text(locations: object) -> str:
    parts: list[str] = []
    for entry in _as_list(locations):
        if isinstance(entry, Mapping):
            parts.extend(
                str(entry[key])
                for key in ("name", "city", "region", "state", "country")
                if entry.get(key)
            )
        elif entry:
            parts.append(str(entry))
    return ", ".join(parts).lower()


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"\d[\d,]*", value)
        if match:
            return float(match.group(0).replace(",", ""))
    return None


def _salary_bounds(salary: object) -> tuple[float | None, float | None]:
    if isinstance(salary, Mapping):
        # Stored jobs use SalaryRange's annualized_*/min_amount keys; raw
        # dictionaries and fixtures may use min/max. Annualized values are
        # preferred because preferences are expressed per year.
        low = _number(
            salary.get("annualized_min")
            or salary.get("min")
            or salary.get("minimum")
            or salary.get("min_amount")
            or salary.get("amount")
        )
        high = _number(
            salary.get("annualized_max")
            or salary.get("max")
            or salary.get("maximum")
            or salary.get("max_amount")
            or salary.get("amount")
        )
        return low, high
    value = _number(salary)
    return value, value


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
