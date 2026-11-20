from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from app.domain.models.candidate import SearchProfile
from app.domain.models.source import SourceConfig
from app.domain.models.tasks import BackgroundTask


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


class InMemorySourceRepository:
    def __init__(self, store: dict[str, SourceConfig]) -> None:
        self._store = store

    async def add(self, source: SourceConfig) -> None:
        self._store[source.id] = source

    async def get(self, source_id: str) -> SourceConfig | None:
        return self._store.get(source_id)

    async def list(self, *, enabled_only: bool = False) -> Sequence[SourceConfig]:
        items = list(self._store.values())
        if enabled_only:
            items = [s for s in items if s.enabled]
        return list(items)

    async def set_enabled(self, source_id: str, enabled: bool) -> None:
        if source_id in self._store:
            from dataclasses import replace

            self._store[source_id] = replace(self._store[source_id], enabled=enabled)

    async def save(self, source: SourceConfig) -> None:
        self._store[source.id] = source

    async def delete(self, source_id: str) -> None:
        self._store.pop(source_id, None)


class InMemoryCompanyRepository:
    def __init__(self, store: dict[str, dict[str, object]]) -> None:
        self._store = store

    async def upsert(self, company_id: str, canonical_name: str, **fields: object) -> None:
        self._store[company_id] = {"id": company_id, "canonical_name": canonical_name, **fields}

    async def get(self, company_id: str) -> Mapping[str, object] | None:
        return self._store.get(company_id)

    async def find_by_name(self, normalized_name: str) -> str | None:
        for cid, c in self._store.items():
            if str(c.get("normalized_name", "")).lower() == normalized_name.lower():
                return cid
        return None

    async def list(self) -> Sequence[Mapping[str, object]]:
        return [dict(v) for v in self._store.values()]


class InMemoryJobRepository:
    def __init__(self, store: dict[str, dict[str, object]]) -> None:
        self._store = store
        self._relations: list[dict[str, object]] = []

    async def upsert(self, job_id, *, company_id, source_id, job, content_hash=None, updated_at_hint=None) -> None:
        self._store[job_id] = {
            "id": job_id,
            "company_id": company_id,
            "source_id": source_id,
            "content_hash": content_hash,
            "updated_at_hint": updated_at_hint,
            "title": getattr(job, "title", None),
            "normalized_title": getattr(job, "normalized_title", None),
            "canonical_url": getattr(job, "canonical_url", None),
            "source_posting_id": getattr(job, "source_posting_id", None),
            "description_text": getattr(job, "description_text", ""),
            "posting_status": "active",
        }

    async def get(self, job_id: str) -> Mapping[str, object] | None:
        return self._store.get(job_id)

    async def find_by_canonical_url(self, canonical_url: str) -> str | None:
        for jid, j in self._store.items():
            if j.get("canonical_url") == canonical_url:
                return jid
        return None

    async def find_by_source_posting(self, source_id: str, source_posting_id: str) -> str | None:
        for jid, j in self._store.items():
            if (
                j.get("source_id") == source_id
                and j.get("source_posting_id") == source_posting_id
            ):
                return jid
        return None

    async def add_relation(self, job_id, related_job_id, relation, confidence=1.0, evidence=None, created_by="user"):
        import datetime
        import uuid
        self._relations.append({
            "id": str(uuid.uuid4()),
            "job_id": job_id,
            "related_job_id": related_job_id,
            "relation": relation,
            "confidence": float(confidence),
            "evidence_json": list(evidence or []),
            "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "created_by": created_by,
        })

    async def list_relations(self, job_id):
        return [r for r in self._relations if r.get("job_id") == job_id or r.get("related_job_id") == job_id]

    async def list(self, *, company_id=None, source_id=None, limit=50, offset=0):
        items = list(self._store.values())
        if company_id:
            items = [j for j in items if j.get("company_id") == company_id]
        if source_id:
            items = [j for j in items if j.get("source_id") == source_id]
        return items[offset : offset + limit]

    async def search(self, query, *, limit=50):
        q = query.lower()
        return [j for j in self._store.values() if q in str(j.get("title", "")).lower()][:limit]

    async def count(self) -> int:
        return len(self._store)

    async def mark_not_seen(self, source_id, seen_ids, cutoff, threshold=2, min_spacing_hours=24):
        from datetime import datetime

        ts = cutoff.isoformat() if hasattr(cutoff, "isoformat") else str(cutoff)
        changed = 0
        for jid, j in list(self._store.items()):
            if j.get("source_id") != source_id:
                continue
            if jid in (seen_ids or []):
                # reset streak
                j["_not_seen_streak"] = 0
                continue
            # increment streak
            streak = int(j.get("_not_seen_streak", 0)) + 1
            j["_not_seen_streak"] = streak
            j["_not_seen_last_at"] = ts
            # spacing check: need first and last at least min_spacing_hours apart
            first = j.get("_not_seen_first_at")
            if first is None:
                j["_not_seen_first_at"] = ts
                first = ts
            # parse
            try:
                t_first = datetime.fromisoformat(str(first).replace("Z", "+00:00"))
                t_now = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                t_first = None
                t_now = None
            spaced = False
            if t_first is not None and t_now is not None:
                spaced = abs((t_now - t_first).total_seconds()) >= min_spacing_hours * 3600
            if streak >= threshold and (streak == 1 or spaced):
                j["posting_status"] = "removed"
                j["last_seen_at"] = ts
                changed += 1
        return changed


class InMemoryObservationRepository:
    def __init__(self, store: list[dict[str, object]]) -> None:
        self._store = store

    async def add(self, *, job_id, source_id, observed_at, posting_status, details=None):
        self._store.append(
            {"job_id": job_id, "source_id": source_id, "observed_at": observed_at, "posting_status": posting_status, "details": details or {}}
        )

    async def list_for_job(self, job_id: str):
        return [d for d in self._store if d["job_id"] == job_id]


class InMemoryCrawlRunRepository:
    def __init__(self, store: dict[str, dict[str, object]]) -> None:
        self._store = store

    async def create(self, run_id, source_id, started_at):
        self._store[run_id] = {"id": run_id, "source_id": source_id, "status": "running", "started_at": started_at}

    async def finish(self, run_id, *, status, finished_at, stats, error=None):
        if run_id in self._store:
            self._store[run_id].update({"status": status, "finished_at": finished_at, "stats": stats, "error": error})

    async def get(self, run_id):
        return self._store.get(run_id)

    async def list(self, *, source_id=None, limit=50):
        items = list(self._store.values())
        if source_id:
            items = [r for r in items if r.get("source_id") == source_id]
        return items[:limit]

    async def last_started_by_source(self):
        latest: dict[str, str] = {}
        for run in self._store.values():
            source_id = run.get("source_id")
            started = run.get("started_at")
            if not isinstance(source_id, str) or started is None:
                continue
            stamp = started.isoformat() if hasattr(started, "isoformat") else str(started)
            if source_id not in latest or stamp > latest[source_id]:
                latest[source_id] = stamp
        return latest

    async def mark_interrupted(self) -> int:
        changed = 0
        for run in self._store.values():
            if run.get("status") == "running":
                run["status"] = "interrupted"
                changed += 1
        return changed

class InMemorySnapshotRepository:
    def __init__(self, store: list[dict[str, object]]) -> None:
        self._store = store

    async def add(self, *, job_id, content_hash, snapshot, created_at) -> None:
        self._store.append(
            {
                "job_id": job_id,
                "content_hash": content_hash,
                "snapshot": dict(snapshot),
                "created_at": created_at,
            }
        )

    async def list_for_job(self, job_id: str):
        return [s for s in self._store if s["job_id"] == job_id]


class InMemoryAliasRepository:
    def __init__(self, store: dict[str, dict[str, object]]) -> None:
        self._store = store

    async def add(self, *, job_id, alias_url, alias_source_id, created_at) -> None:
        self._store.setdefault(
            alias_url,
            {
                "job_id": job_id,
                "alias_url": alias_url,
                "alias_source_id": alias_source_id,
                "created_at": created_at,
            },
        )

    async def find_by_url(self, alias_url: str) -> str | None:
        entry = self._store.get(alias_url)
        return str(entry["job_id"]) if entry else None

    async def list_for_job(self, job_id: str):
        return [a for a in self._store.values() if a["job_id"] == job_id]


class InMemoryUnitOfWork:
    def __init__(
        self,
        settings=None,
        tasks=None,
        plugins=None,
        configs=None,
        sources=None,
        companies=None,
        jobs=None,
        observations=None,
        crawl_runs=None,
        snapshots=None,
        aliases=None,
        search_profiles=None,
    ):
        self._settings_store = settings if settings is not None else {}
        self._tasks_store = tasks if tasks is not None else {}
        self._plugins_store = plugins if plugins is not None else {}
        self._configs_store = configs if configs is not None else {}
        self._sources_store = sources if sources is not None else {}
        self._companies_store = companies if companies is not None else {}
        self._jobs_store = jobs if jobs is not None else {}
        self._observations_store = observations if observations is not None else []
        self._crawl_runs_store = crawl_runs if crawl_runs is not None else {}
        self._snapshots_store = snapshots if snapshots is not None else []
        self._aliases_store = aliases if aliases is not None else {}
        self._search_profiles_store = search_profiles if search_profiles is not None else {}
        self.jobs = InMemoryJobRepository(self._jobs_store)
        self.companies = InMemoryCompanyRepository(self._companies_store)
        self.applications = _Unimplemented()
        self.observations = InMemoryObservationRepository(self._observations_store)
        self.resumes = _Unimplemented()
        self.matches = _Unimplemented()
        self.keywords = _Unimplemented()
        self.settings = InMemorySettingsRepository(self._settings_store)
        self.tasks = InMemoryTaskRepository(self._tasks_store)
        self.plugins = InMemoryPluginStateRepository(self._plugins_store, self._configs_store)
        self.sources = InMemorySourceRepository(self._sources_store)
        self.crawl_runs = InMemoryCrawlRunRepository(self._crawl_runs_store)
        self.snapshots = InMemorySnapshotRepository(self._snapshots_store)
        self.aliases = InMemoryAliasRepository(self._aliases_store)
        self.search_profiles = InMemorySearchProfileRepository(self._search_profiles_store)
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
        self.sources: dict[str, SourceConfig] = {}
        self.companies: dict[str, dict[str, object]] = {}
        self.jobs: dict[str, dict[str, object]] = {}
        self.observations: list[dict[str, object]] = []
        self.crawl_runs: dict[str, dict[str, object]] = {}
        self.snapshots: list[dict[str, object]] = []
        self.aliases: dict[str, dict[str, object]] = {}
        self.search_profiles: dict[str, SearchProfile] = {}

    def __call__(self) -> InMemoryUnitOfWork:
        return InMemoryUnitOfWork(
            self.settings,
            self.tasks,
            self.plugins,
            self.configs,
            self.sources,
            self.companies,
            self.jobs,
            self.observations,
            self.crawl_runs,
            self.snapshots,
            self.aliases,
            self.search_profiles,
        )


class InMemorySearchProfileRepository:
    def __init__(self, store: dict[str, SearchProfile]) -> None:
        self._store = store

    async def add(self, profile: SearchProfile) -> None:
        self._store[profile.id] = profile

    async def get(self, profile_id: str) -> SearchProfile | None:
        return self._store.get(profile_id)

    async def list(self) -> Sequence[SearchProfile]:
        return sorted(self._store.values(), key=lambda p: p.name)

    async def save(self, profile: SearchProfile) -> None:
        self._store[profile.id] = profile

    async def delete(self, profile_id: str) -> None:
        self._store.pop(profile_id, None)
