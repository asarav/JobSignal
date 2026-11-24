"""posting_intelligence.* handlers (M8, never mutates a posting)."""

from __future__ import annotations

from typing import Any

from app.application.dto import (
    EmptyParams,
    JobIdParams,
    PostingIntelligenceCompanyParams,
    PostingIntelligenceConfigParams,
    PostingIntelligenceEvaluateParams,
)
from app.application.intelligence import PostingIntelligenceUseCase


class PostingIntelligenceHandlers:
    def __init__(self, use_case: PostingIntelligenceUseCase, runner: Any = None) -> None:
        self._use_case = use_case
        self._runner = runner

    async def evaluate(self, params: PostingIntelligenceEvaluateParams) -> dict:
        if self._runner is not None:
            task = await self._runner.submit(
                "posting_intelligence.evaluate", params.model_dump()
            )
            return {"task_id": task.id, "evaluated": 0, "skipped": 0}
        result = await self._use_case.evaluate_now(
            job_ids=params.job_ids or None,
            scope=params.scope,
            force=params.force,
            refresh_endpoints=params.refresh_endpoints,
            limit=params.limit or None,
        )
        return {"task_id": None, **result}

    async def get_calibration(self, params: EmptyParams) -> dict:
        return await self._use_case.get_calibration()

    async def get_signals(self, params: JobIdParams) -> dict:
        return await self._use_case.get_signals(params.job_id)

    async def get_risk_history(self, params: JobIdParams) -> dict:
        return await self._use_case.get_risk_history(params.job_id)

    async def get_company_assessment(
        self, params: PostingIntelligenceCompanyParams
    ) -> dict:
        return await self._use_case.get_company_assessment(params.company_id)

    async def configure_model(self, params: PostingIntelligenceConfigParams) -> dict:
        return await self._use_case.configure_model(params.config)
