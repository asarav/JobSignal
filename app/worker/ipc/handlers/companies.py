"""companies.* handlers (read-only)."""

from __future__ import annotations

from app.application.commands.companies import CompaniesUseCase
from app.application.dto import (
    CompanyDetailParams,
    CompanyMergeHistoryParams,
    CompanyMergeParams,
    CompanyTrendParams,
    EmptyParams,
)


class CompaniesHandlers:
    def __init__(self, use_case: CompaniesUseCase) -> None:
        self._use_case = use_case

    async def list(self, params: EmptyParams) -> dict:
        return (await self._use_case.list()).model_dump()

    async def get_detail(self, params: CompanyDetailParams) -> dict:
        return (await self._use_case.get_detail(params.company_id)).model_dump()

    async def get_trend(self, params: CompanyTrendParams) -> dict:
        return (
            await self._use_case.trend(params.company_id, days=params.days)
        ).model_dump()

    async def merge(self, params: CompanyMergeParams) -> dict:
        return (await self._use_case.merge(params)).model_dump()

    async def unmerge(self, params: CompanyDetailParams) -> dict:
        return (await self._use_case.unmerge(params.company_id)).model_dump()

    async def get_merge_history(self, params: CompanyMergeHistoryParams) -> dict:
        return (
            await self._use_case.merge_history(params.company_id, limit=params.limit)
        ).model_dump()
