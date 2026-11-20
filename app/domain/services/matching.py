"""Matching strategies and scoring model (spec sec 11)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from app.domain.models.common import Explanation, Reason
from app.domain.models.matching import DimensionScore, MatchContext, MatchScore, ScoringConfig
from app.domain.ports.matching import MatchingStrategy, ScoringModel

DIMENSIONS = ["skills_resume", "experience_seniority", "responsibilities", "location_workplace", "compensation", "freshness"]
DEFAULT_WEIGHTS = {
    "skills_resume": 0.35,
    "experience_seniority": 0.20,
    "responsibilities": 0.20,
    "location_workplace": 0.10,
    "compensation": 0.10,
    "freshness": 0.05,
}


def _as_list(v: object) -> list[object]:
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return list(v)
    return [v]


class SkillsResumeStrategy(MatchingStrategy):
    id = "match.skills_resume"
    version = "1.0.0"
    dimension = "skills_resume"

    def score(self, profile: Any, job: Mapping[str, Any], ctx: MatchContext) -> DimensionScore:  # type: ignore[override]
        cand_skills = set(getattr(profile, "skills", []) or [])
        req = _as_list(job.get("required_skills"))
        pref = _as_list(job.get("preferred_skills"))
        if not req and not pref:
            # try extract from text
            pass
        # coverage
        def coverage(skills: list[object]) -> tuple[float, bool]:
            if not skills:
                return 1.0, True
            num = 0.0
            den = 0.0
            for s in skills:
                w = 1.0
                den += w
                if s in cand_skills:
                    num += w
            return (num / den if den else 1.0), True

        cov_req, _ = coverage(req) if req else (1.0, True)
        cov_pref, _ = coverage(pref) if pref else (1.0, True)
        if req and pref:
            score = 0.70 * (0.80 * cov_req + 0.20 * cov_pref) + 0.30 * 0.5
        elif req:
            score = 0.70 * cov_req + 0.30 * 0.5
        else:
            score = 0.5
        available = bool(req or pref or cand_skills)
        if not available:
            return DimensionScore(dimension=self.dimension, strategy_id=self.id, strategy_version=self.version, score=None, available=False, weight=0.35, explanation=Explanation(summary="no skills data", reasons=[]))
        missing = [s for s in req if s not in cand_skills]
        reasons = [Reason(code="skills.missing", message=f"missing: {', '.join(str(x) for x in missing)}")] if missing else [Reason(code="skills.ok", message="all required skills present")]
        return DimensionScore(dimension=self.dimension, strategy_id=self.id, strategy_version=self.version, score=max(0.0, min(1.0, score)), available=True, weight=0.35, explanation=Explanation(summary="skills coverage", reasons=reasons))


class ExperienceSeniorityStrategy(MatchingStrategy):
    id = "match.experience_seniority"
    version = "1.0.0"
    dimension = "experience_seniority"

    def score(self, profile: Any, job: Mapping[str, Any], ctx: MatchContext) -> DimensionScore:  # type: ignore[override]
        cand_years = getattr(profile, "years_experience_total", None)
        score = 0.5
        available = False
        if cand_years is not None:
            available = True
            score = min(1.0, (cand_years or 0) / 5.0)
        return DimensionScore(dimension=self.dimension, strategy_id=self.id, strategy_version=self.version, score=score if available else None, available=available, weight=0.20, explanation=Explanation(summary="experience", reasons=[]))


class ResponsibilitiesStrategy(MatchingStrategy):
    id = "match.responsibilities"
    version = "1.0.0"
    dimension = "responsibilities"

    def score(self, profile: Any, job: Mapping[str, Any], ctx: MatchContext) -> DimensionScore:  # type: ignore[override]
        desc = (job.get("description_text") or "")
        available = bool(desc)
        score = 0.5 if available else None
        return DimensionScore(dimension=self.dimension, strategy_id=self.id, strategy_version=self.version, score=score, available=available, weight=0.20, explanation=Explanation(summary="responsibilities", reasons=[]))


class LocationWorkplaceStrategy(MatchingStrategy):
    id = "match.location_workplace"
    version = "1.0.0"
    dimension = "location_workplace"

    def score(self, profile: Any, job: Mapping[str, Any], ctx: MatchContext) -> DimensionScore:  # type: ignore[override]
        available = True
        score = 0.7
        return DimensionScore(dimension=self.dimension, strategy_id=self.id, strategy_version=self.version, score=score, available=available, weight=0.10, explanation=Explanation(summary="location", reasons=[]))


class CompensationStrategy(MatchingStrategy):
    id = "match.compensation"
    version = "1.0.0"
    dimension = "compensation"

    def score(self, profile: Any, job: Mapping[str, Any], ctx: MatchContext) -> DimensionScore:  # type: ignore[override]
        salary = job.get("salary")
        available = salary is not None
        score = 0.6 if available else None
        return DimensionScore(dimension=self.dimension, strategy_id=self.id, strategy_version=self.version, score=score, available=bool(available), weight=0.10, explanation=Explanation(summary="compensation", reasons=[]))


class FreshnessStrategy(MatchingStrategy):
    id = "match.freshness"
    version = "1.0.0"
    dimension = "freshness"

    def score(self, profile: Any, job: Mapping[str, Any], ctx: MatchContext) -> DimensionScore:  # type: ignore[override]
        available = True
        score = 0.8
        return DimensionScore(dimension=self.dimension, strategy_id=self.id, strategy_version=self.version, score=score, available=available, weight=0.05, explanation=Explanation(summary="freshness", reasons=[]))


class WeightedScoringModel(ScoringModel):
    id = "fit.weighted_v1"
    version = "1.0.0"

    def evaluate(self, profile: Any, job: Mapping[str, Any], prefs: Any, strategies: Sequence[MatchingStrategy], cfg: ScoringConfig) -> MatchScore:  # type: ignore[override]
        weights = dict(cfg.weights) if hasattr(cfg, "weights") else dict(DEFAULT_WEIGHTS)
        # normalize
        dim_scores = []
        for s in strategies:
            ds = s.score(profile, job, MatchContext())  # type: ignore[arg-type]
            dim_scores.append(ds)
        # aggregate over available
        num = 0.0
        den = 0.0
        for ds in dim_scores:
            w = weights.get(ds.dimension, ds.weight)
            if ds.available and ds.score is not None:
                num += w * ds.score
                den += w
        fit = (num / den) if den > 0 else 0.0
        coverage = den / sum(weights.get(ds.dimension, ds.weight) for ds in dim_scores) if dim_scores else 0.0
        now = datetime.now(UTC)
        return MatchScore(
            model_id=self.id,
            model_version=self.version,
            fit_score=fit,
            dimension_coverage=coverage,
            dimensions=dim_scores,
            explanation=Explanation(summary="fit score (heuristic)", reasons=[]),
            weights=weights,
            evaluated_at=now,
        )
