"""matching.* handlers."""
from __future__ import annotations

from typing import Any

from app.application.commands.matching import MatchingUseCase
from app.application.dto import (
    MatchingApplyWeightsParams,
    MatchingRunParams,
    MatchingWeightHistoryParams,
    MatchingWeightProposalsParams,
    MatchingWeightsParams,
    ResumeProfileParams,
)
from app.application.weight_proposals import WeightProposalService


class MatchingHandlers:
    def __init__(self, matching: MatchingUseCase, container: Any = None) -> None:
        self._matching = matching
        self._container = container

    async def run(self, params: MatchingRunParams) -> dict:
        results, summary = await self._matching.run_with_summary(
            params.profile_id, params.job_ids
        )
        return {"results": results, **summary}

    async def get_results(self, params: ResumeProfileParams) -> dict:
        return await self._matching.get_results(params.profile_id)

    async def update_weights(self, params: MatchingWeightsParams) -> dict:
        weights = await self._matching.update_weights(params.profile_id, params.weights)
        return {"ok": True, "weights": weights}

    async def get_weight_proposals(
        self, params: MatchingWeightProposalsParams
    ) -> dict:
        return await WeightProposalService(self._container).proposals(
            profile_id=params.profile_id, days=params.days
        )

    async def apply_weight_proposal(
        self, params: MatchingApplyWeightsParams
    ) -> dict:
        return await WeightProposalService(self._container).apply(
            profile_id=params.profile_id,
            weights=params.weights,
            source="proposal" if params.evidence else "manual",
            evidence=params.evidence,
            reason=params.reason,
        )

    async def get_weight_history(
        self, params: MatchingWeightHistoryParams
    ) -> dict:
        return await WeightProposalService(self._container).history(
            profile_id=params.profile_id, limit=params.limit
        )
