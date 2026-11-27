"""The Sources screen page stays bounded when the full seed is registered."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.application.commands.sources import SOURCE_LIST_LIMIT, SourcesUseCase
from app.domain.models.source import SourceConfig


class _Registry:
    def __init__(self, sources: list[SourceConfig]) -> None:
        self._sources = sources

    async def list_sources(self, *, enabled_only: bool = False):  # noqa: ARG002
        return list(self._sources)


def _source(index: int, *, manual: bool) -> SourceConfig:
    schedule = {"interval_s": 3600.0}
    if not manual:
        schedule["probe_status"] = "hiring"
    return SourceConfig(
        id=f"src_{index}",
        url=f"https://boards.example.com/{index}",
        kind="ats",
        connector_id="ats.greenhouse",
        schedule=schedule,
    )


@pytest.mark.asyncio
async def test_list_is_bounded_but_reports_the_full_registry() -> None:
    # Manual boards are appended last, as a user adds them after the seed.
    sources = [_source(index, manual=False) for index in range(600)]
    sources.append(_source(900, manual=True))
    use_case = SourcesUseCase(SimpleNamespace(), _Registry(sources))  # type: ignore[arg-type]

    result = await use_case.list()

    assert len(result.sources) == SOURCE_LIST_LIMIT
    assert result.total == 601
    assert result.truncated is True
    assert result.sources[0].source_id == "src_900"
