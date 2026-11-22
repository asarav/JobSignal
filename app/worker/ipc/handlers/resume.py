"""resume.* handlers."""
from __future__ import annotations

from app.application.commands.resume import ResumeUseCase
from app.application.dto import (
    ResumeImportParams,
    ResumeOverridesParams,
    ResumeParseParams,
    ResumeProfileParams,
    ResumeSetActiveParams,
    ResumeSkillStatusParams,
)


class ResumeHandlers:
    def __init__(self, resume: ResumeUseCase) -> None:
        self._resume = resume

    async def import_resume(self, params: ResumeImportParams) -> dict:
        return await self._resume.import_resume(params.file_path, params.label)

    async def list_versions(self, params: ResumeProfileParams) -> dict:
        rows = await self._resume.list_versions(params.profile_id)
        return {"versions": rows}

    async def get_profile(self, params: ResumeProfileParams) -> dict:
        p = await self._resume.get_profile(params.profile_id)
        return {"profile": p}

    async def set_active(self, params: ResumeSetActiveParams) -> dict:
        await self._resume.set_active(params.profile_id, params.version_id)
        return {"ok": True}

    async def update_overrides(self, params: ResumeOverridesParams) -> dict:
        await self._resume.update_overrides(params.profile_id, params.overrides)
        return {"ok": True}

    async def parse(self, params: ResumeParseParams) -> dict:
        return await self._resume.parse_version(params.resume_version_id)

    async def set_skill_status(self, params: ResumeSkillStatusParams) -> dict:
        await self._resume.set_skill_status(
            params.profile_id, params.keyword_id, params.status
        )
        return {"ok": True}
