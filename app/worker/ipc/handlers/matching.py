"""matching.* handlers."""
from __future__ import annotations

from app.application.commands.matching import MatchingUseCase
from app.application.dto import (
    MatchingRunParams,
    MatchingWeightsParams,
    ResumeProfileParams,
)


class MatchingHandlers:
    def __init__(self, matching: MatchingUseCase) -> None:
        self._matching = matching

    async def run(self, params: MatchingRunParams) -> dict:
        job_ids = params.job_ids if params.job_ids else None
        results = await self._matching.run(params.profile_id, job_ids)
        return {"results": results}

    async def get_results(self, params: ResumeProfileParams) -> dict:
        return await self._matching.get_results(params.profile_id)

    async def update_weights(self, params: MatchingWeightsParams) -> dict:
        weights = await self._matching.update_weights(params.profile_id, params.weights)
        return {"ok": True, "weights": weights}
