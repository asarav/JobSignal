"""applications.* and jobs.set_user_state handlers (M7)."""

from __future__ import annotations

from app.application.commands.applications import ApplicationsUseCase, SuppressionsUseCase
from app.application.dto import (
    ApplicationCreateParams,
    ApplicationEventParams,
    ApplicationOutcomeParams,
    ApplicationStatusParams,
    CompanyHistoryParams,
    EmptyParams,
    JobUserStateParams,
    RestoreSuppressedJobParams,
    SuppressionPolicyParams,
)


class ApplicationsHandlers:
    def __init__(
        self, applications: ApplicationsUseCase, suppressions: SuppressionsUseCase
    ) -> None:
        self._applications = applications
        self._suppressions = suppressions

    async def create(self, params: ApplicationCreateParams) -> dict:
        return (await self._applications.create(params)).model_dump()

    async def update_status(self, params: ApplicationStatusParams) -> dict:
        return (await self._applications.update_status(params)).model_dump()

    async def add_event(self, params: ApplicationEventParams) -> dict:
        return (await self._applications.add_event(params)).model_dump()

    async def record_outcome(self, params: ApplicationOutcomeParams) -> dict:
        return (await self._applications.record_outcome(params)).model_dump()

    async def list(self, params: EmptyParams) -> dict:
        return (await self._applications.list()).model_dump()

    async def get_company_history(self, params: CompanyHistoryParams) -> dict:
        return (await self._applications.get_company_history(params.company_id)).model_dump()

    async def get_suppressions(self, params: EmptyParams) -> dict:
        return (await self._suppressions.list()).model_dump()

    async def get_suppression_policy(self, params: EmptyParams) -> dict:
        return (await self._suppressions.get_policy()).model_dump()

    async def restore_suppressed_job(self, params: RestoreSuppressedJobParams) -> dict:
        return (await self._suppressions.restore(params)).model_dump()

    async def update_suppression_policy(self, params: SuppressionPolicyParams) -> dict:
        return (await self._suppressions.update_policy(params)).model_dump()

    async def set_user_state(self, params: JobUserStateParams) -> dict:
        return (await self._suppressions.set_user_state(params)).model_dump()
