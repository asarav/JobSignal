from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.domain.models.candidate import CandidateProfile, SearchProfile
from app.domain.models.job import JobView
from app.domain.models.matching import DimensionScore, MatchContext, MatchScore, ScoringConfig


class MatchingStrategy(Protocol):
    id: str
    version: str
    dimension: str

    def score(
        self, profile: CandidateProfile, job: JobView, ctx: MatchContext
    ) -> DimensionScore: ...


class ScoringModel(Protocol):
    id: str
    version: str

    def evaluate(
        self,
        profile: CandidateProfile,
        job: JobView,
        prefs: SearchProfile,
        strategies: Sequence[MatchingStrategy],
        cfg: ScoringConfig,
    ) -> MatchScore: ...
