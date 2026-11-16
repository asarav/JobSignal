from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from app.domain.models.posting import (
    JobHistory,
    RiskAssessment,
    RiskModelConfig,
    SignalContext,
    SignalResult,
)


class PostingSignalProvider(Protocol):
    id: str
    version: str
    description: str
    default_weight: float

    def evaluate(self, history: JobHistory, ctx: SignalContext) -> SignalResult: ...


class GhostJobRiskModel(Protocol):
    id: str
    version: str

    def assess(
        self,
        signals: Sequence[SignalResult],
        cfg: RiskModelConfig,
        now: datetime,
    ) -> RiskAssessment: ...
