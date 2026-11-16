from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from app.domain.models.tasks import BackgroundTask
from app.domain.ports.repositories import (
    ApplicationRepository,
    CompanyRepository,
    JobRepository,
    KeywordRepository,
    MatchRepository,
    ObservationRepository,
    ResumeRepository,
)


class _Unimplemented:
    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"repository method '{name}' is available in a later milestone")


class InMemorySettingsRepository:
    def __init__(self, store: dict[str, dict[str, object]]) -> None:
        self._store = store

    async def get(self, key: str) -> Mapping[str, object] | None:
        value = self._store.get(key)
        return copy.deepcopy(value) if value is not None else None

    async def get_all(self) -> Mapping[str, Mapping[str, object]]:
        return copy.deepcopy(self._store)

    async def put(
        self, key: str, value: Mapping[str, object], *, schema_version: int = 1
    ) -> None:
        self._store[key] = dict(value)


class InMemoryTaskRepository:
    def __init__(self, store: dict[str, BackgroundTask]) -> None:
        self._store = store

    async def add(self, task: BackgroundTask) -> None:
        self._store[task.id] = task

    async def get(self, task_id: str) -> BackgroundTask | None:
        return self._store.get(task_id)

    async def list(
        self, *, status: str | None = None, limit: int = 50
    ) -> Sequence[BackgroundTask]:
        items = list(self._store.values())
        if status:
            items = [t for t in items if t.status.value == status]
        items.sort(key=lambda t: t.created_at.isoformat() if t.created_at else "", reverse=True)
        return items[:limit]

    async def save(self, task: BackgroundTask) -> None:
        self._store[task.id] = task

    async def mark_interrupted(self) -> int:
        from dataclasses import replace

        from app.domain.models.enums import TaskStatus

        changed = 0
        for task_id, task in list(self._store.items()):
            if task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
                self._store[task_id] = replace(task, status=TaskStatus.INTERRUPTED)
                changed += 1
        return changed


class InMemoryPluginStateRepository:
    def __init__(
        self,
        plugins: dict[str, dict[str, object]],
        configs: dict[str, dict[str, object]],
    ) -> None:
        self._plugins = plugins
        self._configs = configs

    async def upsert_plugin(
        self,
        plugin_id: str,
        kind: str,
        version: str,
        api_version: str,
        *,
        enabled: bool = True,
        health: Mapping[str, object] | None = None,
    ) -> None:
        existing = self._plugins.get(plugin_id, {})
        self._plugins[plugin_id] = {
            "plugin_id": plugin_id,
            "kind": kind,
            "version": version,
            "api_version": api_version,
            "enabled": existing.get("enabled", enabled),
            "health": dict(health if health is not None else existing.get("health", {})),
        }

    async def list_plugins(self) -> Sequence[Mapping[str, object]]:
        return [copy.deepcopy(v) for v in self._plugins.values()]

    async def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        if plugin_id in self._plugins:
            self._plugins[plugin_id]["enabled"] = enabled

    async def get_health(self, plugin_id: str) -> Mapping[str, object]:
        plugin = self._plugins.get(plugin_id, {})
        health = plugin.get("health", {})
        return dict(health) if isinstance(health, Mapping) else {}

    async def set_health(self, plugin_id: str, health: Mapping[str, object]) -> None:
        if plugin_id in self._plugins:
            self._plugins[plugin_id]["health"] = dict(health)

    async def get_config(self, plugin_id: str) -> Mapping[str, object] | None:
        cfg = self._configs.get(plugin_id)
        return copy.deepcopy(cfg) if cfg is not None else None

    async def set_config(self, plugin_id: str, config: Mapping[str, object]) -> None:
        self._configs[plugin_id] = dict(config)


class InMemoryUnitOfWork:
    def __init__(
        self,
        settings: dict[str, dict[str, object]] | None = None,
        tasks: dict[str, BackgroundTask] | None = None,
        plugins: dict[str, dict[str, object]] | None = None,
        configs: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self._settings_store = settings if settings is not None else {}
        self._tasks_store = tasks if tasks is not None else {}
        self._plugins_store = plugins if plugins is not None else {}
        self._configs_store = configs if configs is not None else {}
        self.jobs: JobRepository = _Unimplemented()  # type: ignore[assignment]
        self.companies: CompanyRepository = _Unimplemented()  # type: ignore[assignment]
        self.applications: ApplicationRepository = _Unimplemented()  # type: ignore[assignment]
        self.observations: ObservationRepository = _Unimplemented()  # type: ignore[assignment]
        self.resumes: ResumeRepository = _Unimplemented()  # type: ignore[assignment]
        self.matches: MatchRepository = _Unimplemented()  # type: ignore[assignment]
        self.keywords: KeywordRepository = _Unimplemented()  # type: ignore[assignment]
        self.settings = InMemorySettingsRepository(self._settings_store)
        self.tasks = InMemoryTaskRepository(self._tasks_store)
        self.plugins = InMemoryPluginStateRepository(self._plugins_store, self._configs_store)
        self.committed = False

    async def __aenter__(self) -> InMemoryUnitOfWork:
        self.committed = False
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True


class InMemoryUnitOfWorkFactory:
    def __init__(self) -> None:
        self.settings: dict[str, dict[str, object]] = {}
        self.tasks: dict[str, BackgroundTask] = {}
        self.plugins: dict[str, dict[str, object]] = {}
        self.configs: dict[str, dict[str, object]] = {}

    def __call__(self) -> InMemoryUnitOfWork:
        return InMemoryUnitOfWork(self.settings, self.tasks, self.plugins, self.configs)
