"""Search profile management (M3)."""

from __future__ import annotations

import uuid

from app.application.container import Container
from app.application.dto import (
    SearchProfileInfo,
    SearchProfileListResult,
    SearchProfileParams,
    SearchProfileSaveParams,
)
from app.domain.models.candidate import SearchProfile


class SearchProfilesUseCase:
    def __init__(self, container: Container) -> None:
        self._container = container

    async def list(self) -> SearchProfileListResult:
        async with self._container.uow_factory() as uow:
            profiles = await uow.search_profiles.list()
            await uow.commit()
        return SearchProfileListResult(
            profiles=[_to_info(p) for p in profiles]
        )

    async def get(self, profile_id: str) -> SearchProfileInfo | None:
        async with self._container.uow_factory() as uow:
            profile = await uow.search_profiles.get(profile_id)
            await uow.commit()
        return _to_info(profile) if profile is not None else None

    async def save(self, params: SearchProfileSaveParams) -> SearchProfileInfo:
        profile = SearchProfile(
            id=params.profile_id or f"sp_{uuid.uuid4().hex[:12]}",
            name=params.name,
            candidate_profile_id=params.candidate_profile_id,
            scoring_config_id=params.scoring_config_id,
            ranking_policy=params.ranking_policy,
            hard_filters=params.hard_filters,
            preferences=params.preferences,
        )
        async with self._container.uow_factory() as uow:
            existing = await uow.search_profiles.get(profile.id)
            if existing is None:
                await uow.search_profiles.add(profile)
            else:
                await uow.search_profiles.save(profile)
            await uow.commit()
        return _to_info(profile)

    async def delete(self, params: SearchProfileParams) -> None:
        async with self._container.uow_factory() as uow:
            await uow.search_profiles.delete(params.profile_id)
            await uow.commit()


def _to_info(profile: SearchProfile) -> SearchProfileInfo:
    return SearchProfileInfo(
        profile_id=profile.id,
        name=profile.name,
        candidate_profile_id=profile.candidate_profile_id,
        scoring_config_id=profile.scoring_config_id,
        ranking_policy=profile.ranking_policy,
        hard_filters=dict(profile.hard_filters),
        preferences=dict(profile.preferences),
    )
