"""Replaceable repository ports. Domain depends only on these Protocols."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from app.domain.models.job import JobView, NormalizedJob
from app.domain.models.source import SourceConfig
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


class SourceRepository(Protocol):
    async def add(self, source: SourceConfig) -> None: ...

    async def get(self, source_id: str) -> SourceConfig | None: ...

    async def list(self, *, enabled_only: bool = False) -> Sequence[SourceConfig]: ...

    async def set_enabled(self, source_id: str, enabled: bool) -> None: ...

    async def save(self, source: SourceConfig) -> None: ...

    async def delete(self, source_id: str) -> None: ...


class CompanyRepository(Protocol):
    async def upsert(
        self, company_id: str, canonical_name: str, **fields: object
    ) -> None: ...

    async def get(self, company_id: str) -> Mapping[str, object] | None: ...

    async def find_by_name(self, normalized_name: str) -> str | None: ...

    async def list(self) -> Sequence[Mapping[str, object]]: ...


class JobRepository(Protocol):
    async def upsert(
        self,
        job_id: str,
        *,
        company_id: str,
        source_id: str,
        job: NormalizedJob | JobView,
        content_hash: str | None = None,
    ) -> None: ...

    async def get(self, job_id: str) -> Mapping[str, object] | None: ...

    async def find_by_canonical_url(self, canonical_url: str) -> str | None: ...

    async def list(
        self,
        *,
        company_id: str | None = None,
        source_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Mapping[str, object]]: ...

    async def search(self, query: str, *, limit: int = 50) -> Sequence[Mapping[str, object]]: ...

    async def count(self) -> int: ...

    async def mark_not_seen(
        self, source_id: str, seen_ids: Sequence[str], cutoff: datetime
    ) -> int: ...


class ObservationRepository(Protocol):
    async def add(
        self,
        *,
        job_id: str,
        source_id: str,
        observed_at: datetime,
        posting_status: str,
        details: Mapping[str, object] | None = None,
    ) -> None: ...

    async def list_for_job(self, job_id: str) -> Sequence[Mapping[str, object]]: ...


class CrawlRunRepository(Protocol):
    async def create(self, run_id: str, source_id: str, started_at: datetime) -> None: ...

    async def finish(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: datetime,
        stats: Mapping[str, object],
        error: str | None = None,
    ) -> None: ...

    async def get(self, run_id: str) -> Mapping[str, object] | None: ...

    async def list(
        self, *, source_id: str | None = None, limit: int = 50
    ) -> Sequence[Mapping[str, object]]: ...


class ResumeRepository(Protocol): ...


class MatchRepository(Protocol): ...


class KeywordRepository(Protocol): ...


class ApplicationRepository(Protocol): ...


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
    sources: SourceRepository
    crawl_runs: CrawlRunRepository

    async def __aenter__(self) -> UnitOfWork: ...

    async def __aexit__(self, *exc: object) -> None: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...
