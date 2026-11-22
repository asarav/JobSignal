"""Unit tests for matching."""
from __future__ import annotations

from types import SimpleNamespace

from app.domain.models.matching import MatchContext, ScoringConfig
from app.domain.services.matching import (
    CompensationStrategy,
    ExperienceSeniorityStrategy,
    FreshnessStrategy,
    LocationWorkplaceStrategy,
    ResponsibilitiesStrategy,
    SkillsResumeStrategy,
    WeightedScoringModel,
)


def test_matching_produces_score():
    profile = SimpleNamespace(skills=["python", "sql"], years_experience_total=3)
    job = {"required_skills": ["python"], "description_text": "build things", "seniority": "mid", "salary": {"min": 100000}}
    strategies = [
        SkillsResumeStrategy(),
        ExperienceSeniorityStrategy(),
        ResponsibilitiesStrategy(),
        LocationWorkplaceStrategy(),
        CompensationStrategy(),
        FreshnessStrategy(),
    ]
    cfg = ScoringConfig(model_id="fit.weighted_v1", model_version="1.0.0", weights={})
    model = WeightedScoringModel()
    result = model.evaluate(profile, job, None, strategies, cfg)
    assert 0.0 <= result.fit_score <= 1.0
    assert len(result.dimensions) == 6


def test_skill_evidence_strength_changes_coverage():
    job = {"required_skills": ["python", "sql"]}
    ctx = MatchContext()
    strategy = SkillsResumeStrategy()

    strong = SimpleNamespace(
        skills=["python", "sql"],
        skill_evidence={"python": "explicit_with_experience", "sql": "explicit"},
    )
    weak = SimpleNamespace(
        skills=["python", "sql"],
        skill_evidence={"python": "limited", "sql": "limited"},
    )
    strong_score = strategy.score(strong, job, ctx).score
    weak_score = strategy.score(weak, job, ctx).score
    assert strong_score is not None and weak_score is not None
    assert strong_score > weak_score


def test_unavailable_dimension_handled():
    profile = SimpleNamespace(skills=[], years_experience_total=None)
    job = {"description_text": ""}
    strategies = [ResponsibilitiesStrategy()]
    cfg = ScoringConfig(model_id="fit.weighted_v1", model_version="1.0.0", weights={})
    model = WeightedScoringModel()
    result = model.evaluate(profile, job, None, strategies, cfg)
    # responsibilities available=False (empty desc) -> score None, not crash
    assert result.dimensions[0].available is False
