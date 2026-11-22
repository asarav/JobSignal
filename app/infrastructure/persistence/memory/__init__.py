from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace as dataclasses_replace
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
        items = [s for s in self._store.values() if not s.archived]
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
        # Archive, never remove: the in-memory twin must behave like the real one.
        if source_id in self._store:
            from dataclasses import replace

            self._store[source_id] = replace(
                self._store[source_id], archived=True, enabled=False
            )


class InMemoryCrawlEventRepository:
    """In-memory twin of the crawl event log (contract-tested both ways)."""

    def __init__(self, store: list[dict[str, object]]) -> None:
        self._store = store

    async def record(
        self,
        *,
        run_id,
        at,
        level,
        code,
        message,
        url=None,
        plugin_id=None,
        data=None,
    ):
        import uuid

        self._store.append(
            {
                "id": f"evt_{uuid.uuid4().hex[:16]}",
                "crawl_run_id": run_id,
                "at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "level": level,
                "code": code,
                "message": message,
                "url": url,
                "plugin_id": plugin_id,
                "data_json": json.dumps(dict(data or {})),
            }
        )

    async def list_for_run(self, run_id, *, limit=200):
        rows = [row for row in self._store if row["crawl_run_id"] == run_id]
        return sorted(rows, key=lambda row: (str(row["at"]), str(row["id"])))[:limit]


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
    def __init__(
        self,
        store: dict[str, dict[str, object]],
        relations: list[dict[str, object]] | None = None,
    ) -> None:
        self._store = store
        # Shared across unit-of-work instances so relations survive a commit.
        self._relations = relations if relations is not None else []

    async def upsert(self, job_id, *, company_id, source_id, job, content_hash=None, updated_at_hint=None) -> None:
        provenance = []
        for entry in getattr(job, "provenance", ()) or ():
            if hasattr(entry, "__dict__"):
                provenance.append(dict(entry.__dict__))
            elif hasattr(entry, "__slots__"):
                provenance.append(
                    {slot: getattr(entry, slot) for slot in entry.__slots__}
                )
        def scalar(name: str):
            value = getattr(job, name, None)
            return value.value if hasattr(value, "value") else value

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
            "locations": getattr(job, "locations", []),
            "workplace_type": scalar("workplace_type"),
            "employment_type": scalar("employment_type"),
            "seniority": scalar("seniority"),
            "salary": getattr(job, "salary", None),
            "posted_at": getattr(job, "posted_at", None),
            "posting_status": "active",
            "provenance": provenance,
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

    async def remove_relation(self, job_id, related_job_id, relation):
        before = len(self._relations)

        def matches(row: dict[str, object]) -> bool:
            if row.get("relation") != relation:
                return False
            pair = (row.get("job_id"), row.get("related_job_id"))
            return pair == (job_id, related_job_id) or pair == (
                related_job_id,
                job_id,
            )

        # Mutate in place: the list is shared with every unit of work.
        self._relations[:] = [row for row in self._relations if not matches(row)]
        return before - len(self._relations)

    async def relation_job_ids(self):
        ids: list[str] = []
        for r in self._relations:
            for key in (r.get("job_id"), r.get("related_job_id")):
                if isinstance(key, str) and key not in ids:
                    ids.append(key)
        return ids

    async def list_by_ids(self, job_ids):
        wanted = {str(job_id) for job_id in job_ids if job_id}
        return [j for jid, j in self._store.items() if jid in wanted]

    async def touch(self, job_id, seen_at):
        job = self._store.get(job_id)
        if job is not None:
            job["last_seen_at"] = seen_at

    async def list(
        self, *, company_id=None, source_id=None, limit=50, offset=0, exclude_job_ids=None
    ):
        excluded = set(exclude_job_ids or ())
        items = [j for jid, j in self._store.items() if jid not in excluded]
        if company_id:
            items = [j for j in items if j.get("company_id") == company_id]
        if source_id:
            items = [j for j in items if j.get("source_id") == source_id]
        return items[offset : offset + limit]

    async def list_for_sources(self, source_ids, *, limit=50):
        wanted = {str(source_id) for source_id in source_ids}
        grouped: dict[str, list[dict[str, object]]] = {
            source_id: [] for source_id in wanted
        }
        for job in self._store.values():
            source_id = job.get("source_id")
            if source_id in wanted and len(grouped[source_id]) < limit:
                grouped[source_id].append(job)
        return grouped

    async def clear_provenance_note(self, job_id, note):
        job = self._store.get(job_id)
        if job is None:
            return False
        entries = job.get("provenance") or []
        changed = False
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            notes = entry.get("notes")
            if isinstance(notes, (list, tuple)) and note in notes:
                entry["notes"] = [value for value in notes if value != note]
                changed = True
        return changed

    async def search(self, query, *, limit=50, offset=0, exclude_job_ids=None):
        q = query.lower()
        excluded = set(exclude_job_ids or ())
        matches = [
            j
            for jid, j in self._store.items()
            if jid not in excluded and q in str(j.get("title", "")).lower()
        ]
        return matches[offset : offset + limit]

    async def filtered_page(
        self,
        filters,
        *,
        query=None,
        source_id=None,
        company_id=None,
        limit=50,
        offset=0,
        exclude_job_ids=None,
    ):
        from app.domain.services.hard_filters import apply_hard_filters

        excluded = set(exclude_job_ids or ())
        items = [j for jid, j in self._store.items() if jid not in excluded]
        if company_id:
            items = [j for j in items if j.get("company_id") == company_id]
        if source_id:
            items = [j for j in items if j.get("source_id") == source_id]
        if query:
            q = query.lower()
            items = [j for j in items if q in str(j.get("title", "")).lower()]
        matches = apply_hard_filters(items, filters)
        return matches[offset : offset + limit], len(matches)

    async def count(self, *, exclude_job_ids=None) -> int:
        excluded = set(exclude_job_ids or ())
        return sum(1 for jid in self._store if jid not in excluded)

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


class InMemoryDedupRepository:
    """Fingerprint history and repost events kept in memory for tests."""

    def __init__(
        self,
        fingerprints: list[dict[str, object]],
        reposts: list[dict[str, object]],
    ) -> None:
        self._fingerprints = fingerprints
        self._reposts = reposts

    async def add_fingerprint(
        self, job_id, fingerprint, *, snapshot_id=None, created_at=None
    ) -> None:
        self._fingerprints.append(
            {
                "job_id": job_id,
                "snapshot_id": snapshot_id,
                "company_id": fingerprint.company_id,
                "url_key": fingerprint.url_key,
                "ats_key": fingerprint.ats_key,
                "title_key": fingerprint.title_key,
                "title_family": fingerprint.title_family,
                "location_key": fingerprint.location_key,
                "desc_exact_hash": fingerprint.desc_exact_hash,
                "desc_simhash64": fingerprint.desc_simhash64,
                "desc_minhash": list(fingerprint.desc_minhash),
                "department": fingerprint.department,
                "salary_min": fingerprint.salary_min,
                "posted_at": fingerprint.posted_at,
                "source_posting_id": fingerprint.source_posting_id,
                "source_id": fingerprint.source_id,
                "required_skills_json": list(fingerprint.req_skills),
                "preferred_skills_json": [],
                "canonical_url": "",
                "salary_json": None,
                "created_at": created_at,
            }
        )

    async def candidate_fingerprints(self, company_id, *, limit=200):
        latest: dict[str, dict[str, object]] = {}
        for row in self._fingerprints:
            if row.get("company_id") != company_id:
                continue
            job_id = str(row.get("job_id"))
            current = latest.get(job_id)
            if current is None or str(row.get("created_at")) >= str(
                current.get("created_at")
            ):
                latest[job_id] = row
        rows = sorted(
            latest.values(), key=lambda row: str(row.get("created_at")), reverse=True
        )
        return rows[:limit]

    async def add_repost_event(
        self,
        *,
        job_id,
        previous_alias_id,
        new_alias_id,
        detected_at,
        repost_class,
        confidence,
        url_changed=False,
        ats_id_changed=False,
        posted_date_reset=False,
        material=False,
        evidence=(),
    ) -> None:
        self._reposts.append(
            {
                "id": f"repost_{len(self._reposts) + 1}",
                "job_id": job_id,
                "previous_alias_id": previous_alias_id,
                "new_alias_id": new_alias_id,
                "detected_at": detected_at,
                "class": repost_class,
                "confidence": float(confidence),
                "url_changed": 1 if url_changed else 0,
                "ats_id_changed": 1 if ats_id_changed else 0,
                "posted_date_reset": 1 if posted_date_reset else 0,
                "material": 1 if material else 0,
                "evidence_json": list(evidence),
            }
        )

    async def list_repost_events(self, job_id):
        return [row for row in self._reposts if row.get("job_id") == job_id]


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

    async def remove(self, job_id: str, alias_url: str) -> bool:
        entry = self._store.get(alias_url)
        if entry is None or str(entry.get("job_id")) != job_id:
            return False
        del self._store[alias_url]
        return True

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
        resumes=None,
        matches=None,
        fingerprints=None,
        reposts=None,
        relations=None,
        applications=None,
        application_events=None,
        application_outcomes=None,
        suppressions=None,
        job_user_state=None,
        suppression_policy=None,
        crawl_events=None,
    ):
        self._settings_store = settings if settings is not None else {}
        self._tasks_store = tasks if tasks is not None else {}
        self._plugins_store = plugins if plugins is not None else {}
        self._configs_store = configs if configs is not None else {}
        self._sources_store = sources if sources is not None else {}
        self._crawl_events_store = crawl_events if crawl_events is not None else []
        self._companies_store = companies if companies is not None else {}
        self._jobs_store = jobs if jobs is not None else {}
        self._observations_store = observations if observations is not None else []
        self._crawl_runs_store = crawl_runs if crawl_runs is not None else {}
        self._snapshots_store = snapshots if snapshots is not None else []
        self._aliases_store = aliases if aliases is not None else {}
        self._fingerprints_store = fingerprints if fingerprints is not None else []
        self._reposts_store = reposts if reposts is not None else []
        self._relations_store = relations if relations is not None else []
        self._applications_store = applications if applications is not None else {}
        self._application_events_store = (
            application_events if application_events is not None else []
        )
        self._application_outcomes_store = (
            application_outcomes if application_outcomes is not None else []
        )
        self._suppressions_store = suppressions if suppressions is not None else {}
        self._suppression_policy_store = suppression_policy if suppression_policy is not None else {}
        self._job_user_state_store = job_user_state if job_user_state is not None else {}
        self._search_profiles_store = search_profiles if search_profiles is not None else {}
        self.jobs = InMemoryJobRepository(self._jobs_store, self._relations_store)
        self.companies = InMemoryCompanyRepository(self._companies_store)
        self.applications = InMemoryApplicationRepository(
            self._applications_store,
            self._application_events_store,
            self._application_outcomes_store,
            self._relations_store,
        )
        self.suppressions = InMemorySuppressionRepository(
            self._suppressions_store, self._suppression_policy_store
        )
        self.job_user_state = InMemoryJobUserStateRepository(
            self._job_user_state_store
        )
        self.observations = InMemoryObservationRepository(self._observations_store)
        self.resumes = resumes if resumes is not None else InMemoryResumeRepository()
        self.matches = matches if matches is not None else InMemoryMatchRepository()
        self.keywords = _Unimplemented()
        self.settings = InMemorySettingsRepository(self._settings_store)
        self.tasks = InMemoryTaskRepository(self._tasks_store)
        self.plugins = InMemoryPluginStateRepository(self._plugins_store, self._configs_store)
        self.sources = InMemorySourceRepository(self._sources_store)
        self.crawl_runs = InMemoryCrawlRunRepository(self._crawl_runs_store)
        self.crawl_events = InMemoryCrawlEventRepository(self._crawl_events_store)
        self.snapshots = InMemorySnapshotRepository(self._snapshots_store)
        self.aliases = InMemoryAliasRepository(self._aliases_store)
        self.dedup = InMemoryDedupRepository(
            self._fingerprints_store, self._reposts_store
        )
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
        self.crawl_events: list[dict[str, object]] = []
        self.snapshots: list[dict[str, object]] = []
        self.aliases: dict[str, dict[str, object]] = {}
        self.fingerprints: list[dict[str, object]] = []
        self.reposts: list[dict[str, object]] = []
        self.relations: list[dict[str, object]] = []
        self.search_profiles: dict[str, SearchProfile] = {}
        self.resumes = InMemoryResumeRepository()
        self.matches = InMemoryMatchRepository()
        self.applications: dict[str, Any] = {}
        self.application_events: list[dict[str, object]] = []
        self.application_outcomes: list[dict[str, object]] = []
        self.suppressions: dict[tuple[str, str], Any] = {}
        self.suppression_policy: dict[str, dict[str, object]] = {}
        self.job_user_state: dict[str, dict[str, object]] = {}

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
            self.resumes,
            self.matches,
            self.fingerprints,
            self.reposts,
            self.relations,
            self.applications,
            self.application_events,
            self.application_outcomes,
            self.suppressions,
            self.job_user_state,
            self.suppression_policy,
            crawl_events=self.crawl_events,
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

# append to memory module


class InMemoryResumeRepository:
    def __init__(self):
        self._profiles = {}
        self._files = {}
        self._versions = {}
        self._skills = {}
        self._experience = {}
        self._education = {}
        self._items = {}
        self._statuses = {}

    async def add_profile(self, profile_id, display_name):
        self._profiles[profile_id] = {"id": profile_id, "display_name": display_name, "active_resume_version_id": None, "contact_json": "{}", "overrides_json": "{}"}

    async def get_profile(self, profile_id):
        return self._profiles.get(profile_id)

    async def add_resume_file(self, file_id, sha256, original_name, mime_type, size_bytes, stored_path):
        self._files[file_id] = {"id": file_id, "sha256": sha256, "original_name": original_name, "stored_path": stored_path}

    async def get_resume_file_by_sha256(self, sha256):
        for row in self._files.values():
            if row.get("sha256") == sha256:
                return row
        return None

    async def add_resume_version(self, version_id, profile_id, file_id, label, parser_id, parser_version, text_hash, parsed_json, parent_version_id=None, is_active=False):
        self._versions[version_id] = {"id": version_id, "candidate_profile_id": profile_id, "resume_file_id": file_id, "label": label, "parser_id": parser_id, "parser_version": parser_version, "text_hash": text_hash, "parsed_json": parsed_json, "parent_version_id": parent_version_id, "is_active": is_active}

    async def get_resume_file(self, file_id):
        return self._files.get(file_id)

    async def set_skill_status(self, profile_id, keyword_id, status):
        self._statuses.setdefault(profile_id, {})[keyword_id] = status

    async def get_skill_status(self, profile_id):
        return dict(self._statuses.get(profile_id, {}))

    async def list_versions(self, profile_id):
        rows = []
        for v in self._versions.values():
            if v["candidate_profile_id"] != profile_id:
                continue
            row = dict(v)
            f = self._files.get(v.get("resume_file_id"))
            if f:
                row["original_name"] = f.get("original_name")
                row["stored_path"] = f.get("stored_path")
            rows.append(row)
        return rows

    async def set_active(self, profile_id, version_id):
        for v in self._versions.values():
            if v["candidate_profile_id"] == profile_id:
                v["is_active"] = (v["id"] == version_id)
        if profile_id in self._profiles:
            self._profiles[profile_id]["active_resume_version_id"] = version_id

    async def get_version(self, version_id):
        return self._versions.get(version_id)

    async def save_skills(self, version_id, skills):
        self._skills[version_id] = [dict(skill) for skill in skills]

    async def get_skills(self, version_id):
        return [dict(row) for row in self._skills.get(version_id, [])]

    async def save_experience(self, version_id, entries):
        self._experience[version_id] = [dict(entry) for entry in entries]

    async def get_experience(self, version_id):
        return [dict(row) for row in self._experience.get(version_id, [])]

    async def save_education(self, version_id, entries):
        self._education[version_id] = [dict(entry) for entry in entries]

    async def get_education(self, version_id):
        return [dict(row) for row in self._education.get(version_id, [])]

    async def save_profile_items(self, version_id, kind, items):
        self._items[(version_id, kind)] = [dict(item) for item in items]

    async def get_profile_items(self, version_id, kind):
        return [dict(row) for row in self._items.get((version_id, kind), [])]

    async def update_overrides(self, profile_id, overrides):
        import json
        if profile_id in self._profiles:
            self._profiles[profile_id]["overrides_json"] = json.dumps(overrides)

    async def update_contact(self, profile_id, contact):
        import json
        if profile_id in self._profiles:
            self._profiles[profile_id]["contact_json"] = json.dumps(contact)

    async def get_overrides(self, profile_id):
        import json
        p = self._profiles.get(profile_id)
        if not p:
            return {}
        try:
            return json.loads(p.get("overrides_json") or "{}")
        except Exception:
            return {}


class InMemoryMatchRepository:
    def __init__(self):
        self._results = []
        self._dimensions = {}
        self._explanations = {}
        self._weights = {}

    async def save_result(self, result_id, job_id, profile_id, resume_version_id, model_id, model_version, weights, input_hash, fit_score, dimension_coverage, dimensions, evaluated_at, explanations=None):
        self._results.append({
            "id": result_id, "job_id": job_id, "candidate_profile_id": profile_id,
            "resume_version_id": resume_version_id, "model_id": model_id,
            "model_version": model_version, "weights_json": __import__("json").dumps(weights),
            "input_hash": input_hash, "fit_score": fit_score,
            "dimension_coverage": dimension_coverage, "evaluated_at": evaluated_at,
            "job_title": None, "company_name": None,
        })
        self._dimensions[result_id] = [dict(dimension) for dimension in dimensions]
        self._explanations[result_id] = [dict(item) for item in (explanations or [])]

    async def list_results(self, profile_id, limit=50):
        rows = [r for r in self._results if r["candidate_profile_id"] == profile_id]
        rows.sort(key=lambda r: (-float(r.get("fit_score") or 0), str(r.get("evaluated_at") or "")))
        return rows[:limit]

    async def find_result(self, profile_id, job_id, input_hash):
        for row in self._results:
            if (row["candidate_profile_id"] == profile_id and row["job_id"] == job_id
                    and row["input_hash"] == input_hash):
                return row
        return None

    async def delete_results(self, profile_id, job_id):
        keep = []
        for row in self._results:
            if row["candidate_profile_id"] == profile_id and row["job_id"] == job_id:
                self._dimensions.pop(row["id"], None)
                self._explanations.pop(row["id"], None)
                continue
            keep.append(row)
        self._results = keep

    async def list_dimensions(self, result_id):
        return list(self._dimensions.get(result_id, []))

    async def list_explanations(self, result_id):
        return list(self._explanations.get(result_id, []))

    async def get_weights(self, profile_id):
        return self._weights.get(profile_id)

    async def set_weights(self, profile_id, weights):
        self._weights[profile_id] = dict(weights)


class InMemoryApplicationRepository:
    """Application history in memory; the event log is append-only here too."""

    def __init__(
        self,
        store: dict[str, Any],
        events: list[dict[str, object]],
        outcomes: list[dict[str, object]],
        relations: list[dict[str, object]],
    ) -> None:
        self._store = store
        self._events = events
        self._outcomes = outcomes
        self._relations = relations

    async def add(self, application: Any) -> None:
        self._store[application.id] = application

    async def get(self, application_id: str) -> Any:
        return self._store.get(application_id)

    async def list(self, *, limit: int = 50, offset: int = 0):
        rows = sorted(
            self._store.values(),
            key=lambda a: (a.applied_at or a.created_at, a.id),
            reverse=True,
        )
        return rows[offset : offset + limit]

    async def save(
        self,
        application_id: str,
        *,
        status: str,
        applied_at: Any,
        notes: str,
        resume_version_id: str | None,
        updated_at: Any,
    ):
        current = self._store.get(application_id)
        if current is None:
            return None
        updated = dataclasses_replace(
            current,
            status=status,
            applied_at=applied_at,
            notes=notes,
            resume_version_id=resume_version_id,
            updated_at=updated_at,
        )
        self._store[application_id] = updated
        return updated

    async def find_for_job(self, job_id: str):
        return [a for a in self._store.values() if a.job_id == job_id]

    async def list_applied(self, *, company_id: str | None = None):
        from app.domain.models.application import AppliedJob

        out: list[AppliedJob] = []
        for application in self._store.values():
            if application.applied_at is None:
                continue
            if company_id and application.company_id != company_id:
                continue
            related: list[str] = []
            for row in self._relations:
                if row.get("relation") == "user_unmerged":
                    continue
                if row.get("job_id") == application.job_id:
                    related.append(str(row.get("related_job_id")))
                elif row.get("related_job_id") == application.job_id:
                    related.append(str(row.get("job_id")))
            out.append(
                AppliedJob(
                    application_id=application.id,
                    job_id=application.job_id,
                    company_id=application.company_id,
                    title=application.job_title,
                    title_family=application.job_title_family,
                    description_text=application.job_description_text,
                    canonical_url=application.job_canonical_url,
                    applied_at=application.applied_at,
                    department=application.job_department,
                    locations=application.job_locations,
                    workplace_type=application.job_workplace_type,
                    related_job_ids=tuple(related),
                )
            )
        return out

    async def add_event(self, event: Any) -> None:
        self._events.append(event)

    async def list_events(self, application_id: str):
        return [e for e in self._events if e.application_id == application_id]

    async def add_outcome(self, outcome: Any) -> None:
        self._outcomes.append(outcome)

    async def list_outcomes(self, application_id: str):
        return [o for o in self._outcomes if o.application_id == application_id]


class InMemorySuppressionRepository:
    def __init__(
        self,
        store: dict[tuple[str, str], Any],
        policies: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self._store = store
        self._policies = policies if policies is not None else {}

    async def save(self, record: Any) -> None:
        key = (record.job_id, record.application_id)
        current = self._store.get(key)
        if current is not None and current.state == "restored":
            return
        self._store[key] = record

    async def get(self, job_id: str, application_id: str):
        return self._store.get((job_id, application_id))

    async def list(self, *, state: str | None = None, limit: int = 200):
        rows = [r for r in self._store.values() if state is None or r.state == state]
        rows.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        return rows[:limit]

    async def list_for_job(self, job_id: str):
        return sorted(
            (r for r in self._store.values() if r.job_id == job_id),
            key=lambda r: (r.created_at, r.id),
        )

    async def active_job_ids(self, now):
        return sorted(
            {
                r.job_id
                for r in self._store.values()
                if r.state == "active" and (r.expires_at is None or r.expires_at > now)
            }
        )

    async def restore(self, job_id: str, application_id: str, at: Any) -> bool:
        key = (job_id, application_id)
        current = self._store.get(key)
        if current is None or current.state == "restored":
            return False
        self._store[key] = dataclasses_replace(current, state="restored", updated_at=at)
        return True

    async def expire(self, now: Any):
        lapsed = []
        for key, record in list(self._store.items()):
            if record.state != "active" or record.expires_at is None:
                continue
            if record.expires_at > now:
                continue
            updated = dataclasses_replace(record, state="expired", updated_at=now)
            self._store[key] = updated
            lapsed.append(updated)
        return lapsed

    async def clear_restored(self, job_id: str, application_id: str) -> bool:
        key = (job_id, application_id)
        current = self._store.get(key)
        if current is None or current.state != "restored":
            return False
        del self._store[key]
        return True

    async def clear_job(self, job_id: str) -> int:
        keys = [
            key
            for key, record in self._store.items()
            if key[0] == job_id and record.state != "restored"
        ]
        for key in keys:
            del self._store[key]
        return len(keys)

    async def get_policy(self):
        return self._policies.get("default")

    async def put_policy(self, policy, at) -> None:
        self._policies["default"] = dict(policy)

    async def delete_for_application(self, application_id: str) -> int:
        keys = [k for k in self._store if k[1] == application_id]
        for key in keys:
            del self._store[key]
        return len(keys)


class InMemoryJobUserStateRepository:
    def __init__(self, store: dict[str, dict[str, object]]) -> None:
        self._store = store

    async def set(self, job_id: str, state: str, *, note, updated_at) -> None:
        self._store[job_id] = {
            "job_id": job_id,
            "state": state,
            "note": note,
            "updated_at": updated_at.isoformat()
            if hasattr(updated_at, "isoformat")
            else str(updated_at),
        }

    async def get(self, job_id: str):
        return self._store.get(job_id)

    async def get_many(self, job_ids):
        return {
            job_id: str(self._store[job_id]["state"])
            for job_id in job_ids
            if job_id in self._store
        }

    async def list(self, *, state: str | None = None, limit: int = 200):
        rows = [
            row
            for row in self._store.values()
            if state is None or row.get("state") == state
        ]
        return rows[:limit]

    async def hidden_job_ids(self):
        return sorted(
            job_id
            for job_id, row in self._store.items()
            if row.get("state") in ("dismissed", "hidden")
        )

    async def clear(self, job_id: str) -> bool:
        return self._store.pop(job_id, None) is not None


__all__ = [
    "InMemoryApplicationRepository",
    "InMemoryJobUserStateRepository",
    "InMemorySuppressionRepository",
    "InMemoryUnitOfWork",
    "InMemoryUnitOfWorkFactory",
]
