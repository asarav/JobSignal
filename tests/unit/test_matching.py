"""Unit tests for matching."""
from __future__ import annotations

from types import SimpleNamespace

from app.domain.models.matching import ScoringConfig
from app.domain.services.matching import (
    WeightedScoringModel,
    SkillsResumeStrategy,
    ExperienceSeniorityStrategy,
    ResponsibilitiesStrategy,
    LocationWorkplaceStrategy,
    CompensationStrategy,
    FreshnessStrategy,
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


def test_unavailable_dimension_handled():
    profile = SimpleNamespace(skills=[], years_experience_total=None)
    job = {"description_text": ""}
    strategies = [ResponsibilitiesStrategy()]
    cfg = ScoringConfig(model_id="fit.weighted_v1", model_version="1.0.0", weights={})
    model = WeightedScoringModel()
    result = model.evaluate(profile, job, None, strategies, cfg)
    # responsibilities available=False (empty desc) -> score None, not crash
    assert result.dimensions[0].available is False
