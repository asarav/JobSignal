from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.models.common import Explanation


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    model_id: str
    model_version: str
    weights: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class MatchContext:
    search_profile_id: str | None = None
    now: datetime | None = None


@dataclass(frozen=True, slots=True)
class DimensionScore:
    dimension: str
    strategy_id: str
    strategy_version: str
    score: float | None
    available: bool
    weight: float
    explanation: Explanation | None = None


@dataclass(frozen=True, slots=True)
class MatchScore:
    model_id: str
    model_version: str
    fit_score: float
    dimension_coverage: float
    dimensions: Sequence[DimensionScore]
    explanation: Explanation
    weights: Mapping[str, float] = field(default_factory=dict)
    evaluated_at: datetime | None = None
