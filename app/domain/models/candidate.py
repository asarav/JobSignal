from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CandidateProfile:
    id: str
    display_name: str
    resume_version_id: str | None = None
    skills: Sequence[str] = ()
    years_experience_total: float | None = None
    answers: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchProfile:
    id: str
    name: str
    candidate_profile_id: str
    scoring_config_id: str | None = None
    ranking_policy: str = "deprioritize"
    hard_filters: Mapping[str, object] = field(default_factory=dict)
    preferences: Mapping[str, object] = field(default_factory=dict)
