"""Unit tests for M3 search profiles use case."""

from __future__ import annotations

from app.application.commands.search_profiles import SearchProfilesUseCase
from app.application.dto import (
    SearchProfileListResult,
    SearchProfileParams,
    SearchProfileSaveParams,
)
from app.domain.models.candidate import SearchProfile
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory


class _Container:
    def __init__(self) -> None:
        self.uow_factory = InMemoryUnitOfWorkFactory()


async def test_save_and_list_profiles():
    container = _Container()
    usecase = SearchProfilesUseCase(container)

    saved = await usecase.save(
        SearchProfileSaveParams(
            name="Python Jobs",
            hard_filters={"workplace_types": ["remote"]},
        )
    )
    assert saved.name == "Python Jobs"
    assert saved.profile_id is not None
    assert saved.hard_filters == {"workplace_types": ["remote"]}

    listed = await usecase.list()
    assert isinstance(listed, SearchProfileListResult)
    assert len(listed.profiles) == 1
    assert listed.profiles[0].profile_id == saved.profile_id
    assert listed.profiles[0].name == "Python Jobs"


async def test_save_generates_id_when_missing():
    container = _Container()
    usecase = SearchProfilesUseCase(container)

    saved = await usecase.save(SearchProfileSaveParams(name="No ID Profile"))
    assert saved.profile_id.startswith("sp_")


async def test_save_updates_existing_profile():
    container = _Container()
    usecase = SearchProfilesUseCase(container)

    saved = await usecase.save(SearchProfileSaveParams(name="Original"))
    updated = await usecase.save(
        SearchProfileSaveParams(
            profile_id=saved.profile_id,
            name="Renamed",
            hard_filters={"employment_types": ["contract"]},
        )
    )
    assert updated.profile_id == saved.profile_id
    assert updated.name == "Renamed"
    assert updated.hard_filters == {"employment_types": ["contract"]}

    listed = await usecase.list()
    assert len(listed.profiles) == 1
    assert listed.profiles[0].name == "Renamed"


async def test_get_and_delete_profile():
    container = _Container()
    usecase = SearchProfilesUseCase(container)

    saved = await usecase.save(SearchProfileSaveParams(name="To Delete"))

    got = await usecase.get(saved.profile_id)
    assert got is not None
    assert got.name == "To Delete"

    await usecase.delete(SearchProfileParams(profile_id=saved.profile_id))

    got_after = await usecase.get(saved.profile_id)
    assert got_after is None

    listed = await usecase.list()
    assert len(listed.profiles) == 0


async def test_delete_unknown_profile_is_noop():
    container = _Container()
    usecase = SearchProfilesUseCase(container)

    await usecase.delete(SearchProfileParams(profile_id="does-not-exist"))

    listed = await usecase.list()
    assert listed.profiles == []


async def test_list_is_sorted_by_name():
    container = _Container()
    usecase = SearchProfilesUseCase(container)

    await usecase.save(SearchProfileSaveParams(name="Zebra"))
    await usecase.save(SearchProfileSaveParams(name="Alpha"))
    await usecase.save(SearchProfileSaveParams(name="Middle"))

    listed = await usecase.list()
    names = [p.name for p in listed.profiles]
    assert names == ["Alpha", "Middle", "Zebra"]