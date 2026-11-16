"""S6: ConstantScoringModel in container; recorded model_id."""

import pytest
from app.domain.models.candidate import CandidateProfile
from app.domain.models.job import JobView
from app.domain.models.matching import MatchContext, ScoringConfig
from tests.support.toy_plugins import ConstantScoringModel


@pytest.mark.asyncio
async def test_constant_scoring_model_recorded_model_id():
    model = ConstantScoringModel(score=0.75)
    profile = CandidateProfile(id="p1", display_name="Test")
    job = JobView(
    id="j1",
    title="T",
    company_id="c1",
    company_name="C",
    canonical_url="https://x.com",
    description_text="",
    workplace_type="remote",
    employment_type="full_time",
    seniority="mid",
)
    cfg = ScoringConfig(model_id="match.constant", model_version="1.0.0", weights={})
    result = await model.evaluate(profile, job, MatchContext(), [], cfg)
    assert model.recorded_model_id == "match.constant"
    assert result.fit_score == 0.75