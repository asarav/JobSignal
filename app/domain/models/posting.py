from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.models.common import Evidence, Explanation
from app.domain.models.enums import RiskClass, SignalStatus


@dataclass(frozen=True, slots=True)
class JobHistory:
    job_id: str
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    snapshot_count: int = 0
    observations: Sequence[Mapping[str, object]] = ()


@dataclass(frozen=True, slots=True)
class SignalContext:
    job_id: str
    now: datetime | None = None


@dataclass(frozen=True, slots=True)
class SignalResult:
    signal_id: str
    provider_version: str
    status: SignalStatus
    risk_contribution: float | None
    confidence: float
    strong: bool = False
    evidence: Sequence[Evidence] = ()
    missing_reason: str | None = None
    explanation: str = ""


@dataclass(frozen=True, slots=True)
class RiskModelConfig:
    model_id: str
    model_version: str
    prior_risk: float = 0.20
    prior_weight: float = 0.15
    weights: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    model_id: str
    model_version: str
    risk: float
    confidence: float
    risk_class: RiskClass
    explanation: Explanation
    signals: Sequence[SignalResult] = ()
    evaluated_at: datetime | None = None
