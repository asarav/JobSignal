from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from app.domain.models.tasks import BackgroundTask


class SettingsRepository(Protocol):
    async def get(self, key: str) -> Mapping[str, object] | None: ...

    async def get_all(self) -> Mapping[str, Mapping[str, object]]: ...

    async def put(
        self, key: str, value: Mapping[str, object], *, schema_version: int = 1
    ) -> None: ...


class TaskRepository(Protocol):
    async def add(self, task: BackgroundTask) -> None: ...

    async def get(self, task_id: str) -> BackgroundTask | None: ...

    async def list(
        self, *, status: str | None = None, limit: int = 50
    ) -> Sequence[BackgroundTask]: ...

    async def save(self, task: BackgroundTask) -> None: ...

    async def mark_interrupted(self) -> int:
        """Mark queued/running tasks left by a previous process as interrupted."""
        ...


class JobRepository(Protocol): ...


class CompanyRepository(Protocol): ...


class ApplicationRepository(Protocol): ...


class ObservationRepository(Protocol): ...


class ResumeRepository(Protocol): ...


class MatchRepository(Protocol): ...


class KeywordRepository(Protocol): ...


class PluginStateRepository(Protocol):
    async def upsert_plugin(
        self,
        plugin_id: str,
        kind: str,
        version: str,
        api_version: str,
        *,
        enabled: bool = True,
        health: Mapping[str, object] | None = None,
    ) -> None: ...

    async def list_plugins(self) -> Sequence[Mapping[str, object]]: ...

    async def set_enabled(self, plugin_id: str, enabled: bool) -> None: ...

    async def get_health(self, plugin_id: str) -> Mapping[str, object]: ...

    async def set_health(self, plugin_id: str, health: Mapping[str, object]) -> None: ...

    async def get_config(self, plugin_id: str) -> Mapping[str, object] | None: ...

    async def set_config(self, plugin_id: str, config: Mapping[str, object]) -> None: ...


class UnitOfWork(Protocol):
    jobs: JobRepository
    companies: CompanyRepository
    applications: ApplicationRepository
    settings: SettingsRepository
    observations: ObservationRepository
    resumes: ResumeRepository
    matches: MatchRepository
    keywords: KeywordRepository
    tasks: TaskRepository
    plugins: PluginStateRepository

    async def __aenter__(self) -> UnitOfWork: ...

    async def __aexit__(self, *exc: object) -> None: ...

    async def commit(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...
