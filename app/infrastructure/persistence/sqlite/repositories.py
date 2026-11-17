from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from app.domain.models.enums import TaskStatus
from app.domain.models.source import SourceConfig
from app.domain.models.tasks import BackgroundTask


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SqliteSettingsRepository:
    def __init__(self, conn: aiosqlite.Connection, clock_now: Any) -> None:
        self._conn = conn
        self._now = clock_now

    async def get(self, key: str) -> Mapping[str, object] | None:
        cur = await self._conn.execute(
            "SELECT value_json FROM app_settings WHERE key = ?", (key,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        return data if isinstance(data, dict) else {"value": data}

    async def get_all(self) -> Mapping[str, Mapping[str, object]]:
        cur = await self._conn.execute("SELECT key, value_json FROM app_settings")
        rows = await cur.fetchall()
        result: dict[str, Mapping[str, object]] = {}
        for key, raw in rows:
            data = json.loads(raw)
            result[str(key)] = data if isinstance(data, dict) else {"value": data}
        return result

    async def put(
        self, key: str, value: Mapping[str, object], *, schema_version: int = 1
    ) -> None:
        now = self._now().strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = json.dumps(dict(value), sort_keys=True)
        await self._conn.execute(
            """
            INSERT INTO app_settings(key, value_json, schema_version, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              value_json = excluded.value_json,
              schema_version = excluded.schema_version,
              updated_at = excluded.updated_at
            """,
            (key, payload, schema_version, now),
        )


class SqliteTaskRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    def _row(self, row: aiosqlite.Row) -> BackgroundTask:
        return BackgroundTask(
            id=row["id"],
            type=row["type"],
            status=TaskStatus(row["status"]),
            priority=row["priority"],
            params=json.loads(row["params_json"] or "{}"),
            progress=json.loads(row["progress_json"] or "{}"),
            error=json.loads(row["error_json"]) if row["error_json"] else None,
            created_at=_parse_dt(row["created_at"]),
            started_at=_parse_dt(row["started_at"]),
            finished_at=_parse_dt(row["finished_at"]),
            parent_task_id=row["parent_task_id"],
        )

    async def add(self, task: BackgroundTask) -> None:
        await self._save(task, insert=True)

    async def save(self, task: BackgroundTask) -> None:
        await self._save(task, insert=False)

    async def _save(self, task: BackgroundTask, *, insert: bool) -> None:
        def fmt(dt: datetime | None) -> str | None:
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None

        values = (
            task.type,
            task.status.value,
            task.priority,
            json.dumps(dict(task.params), sort_keys=True),
            json.dumps(dict(task.progress), sort_keys=True),
            json.dumps(dict(task.error), sort_keys=True) if task.error else None,
            fmt(task.created_at) or "",
            fmt(task.started_at),
            fmt(task.finished_at),
            task.parent_task_id,
            task.id,
        )
        if insert:
            await self._conn.execute(
                """
                INSERT INTO background_tasks(
                  type, status, priority, params_json, progress_json, error_json,
                  created_at, started_at, finished_at, parent_task_id, id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        else:
            await self._conn.execute(
                """
                UPDATE background_tasks SET
                  type=?, status=?, priority=?, params_json=?, progress_json=?,
                  error_json=?, created_at=?, started_at=?, finished_at=?, parent_task_id=?
                WHERE id=?
                """,
                values,
            )

    async def get(self, task_id: str) -> BackgroundTask | None:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM background_tasks WHERE id = ?", (task_id,)
        )
        row = await cur.fetchone()
        return self._row(row) if row else None

    async def list(
        self, *, status: str | None = None, limit: int = 50
    ) -> Sequence[BackgroundTask]:
        self._conn.row_factory = aiosqlite.Row
        if status:
            cur = await self._conn.execute(
                "SELECT * FROM background_tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cur = await self._conn.execute(
                "SELECT * FROM background_tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [self._row(r) for r in await cur.fetchall()]

    async def mark_interrupted(self) -> int:
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = await self._conn.execute(
            "UPDATE background_tasks SET status = ?, finished_at = ? "
            "WHERE status IN (?, ?)",
            ("interrupted", now, "queued", "running"),
        )
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


class SqlitePluginStateRepository:
    def __init__(self, conn: aiosqlite.Connection, clock_now: Any) -> None:
        self._conn = conn
        self._now = clock_now

    def _ts(self) -> str:
        return self._now().strftime("%Y-%m-%dT%H:%M:%SZ")

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
        health_json = json.dumps(dict(health or {}), sort_keys=True)
        await self._conn.execute(
            """
            INSERT INTO plugin_registry(
                plugin_id, kind, version, api_version, enabled, health_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plugin_id) DO UPDATE SET
              kind=excluded.kind,
              version=excluded.version,
              api_version=excluded.api_version,
              health_json=excluded.health_json,
              updated_at=excluded.updated_at
            """,
            (plugin_id, kind, version, api_version, 1 if enabled else 0, health_json, self._ts()),
        )

    async def list_plugins(self) -> Sequence[Mapping[str, object]]:
        cur = await self._conn.execute(
            "SELECT plugin_id, kind, version, api_version, enabled, health_json "
            "FROM plugin_registry"
        )
        rows = await cur.fetchall()
        items: list[dict[str, object]] = []
        for plugin_id, kind, version, api_version, enabled, health_json in rows:
            items.append(
                {
                    "plugin_id": plugin_id,
                    "kind": kind,
                    "version": version,
                    "api_version": api_version,
                    "enabled": bool(enabled),
                    "health": json.loads(health_json or "{}"),
                }
            )
        return items

    async def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        await self._conn.execute(
            "UPDATE plugin_registry SET enabled = ?, updated_at = ? WHERE plugin_id = ?",
            (1 if enabled else 0, self._ts(), plugin_id),
        )

    async def get_health(self, plugin_id: str) -> Mapping[str, object]:
        cur = await self._conn.execute(
            "SELECT health_json FROM plugin_registry WHERE plugin_id = ?", (plugin_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return {}
        return json.loads(row[0] or "{}")

    async def set_health(self, plugin_id: str, health: Mapping[str, object]) -> None:
        await self._conn.execute(
            "UPDATE plugin_registry SET health_json = ?, updated_at = ? WHERE plugin_id = ?",
            (json.dumps(dict(health), sort_keys=True), self._ts(), plugin_id),
        )

    async def get_config(self, plugin_id: str) -> Mapping[str, object] | None:
        cur = await self._conn.execute(
            "SELECT config_json FROM plugin_config WHERE plugin_id = ?", (plugin_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        return data if isinstance(data, dict) else {"value": data}

    async def set_config(self, plugin_id: str, config: Mapping[str, object]) -> None:
        await self._conn.execute(
            """
            INSERT INTO plugin_config(plugin_id, config_json, config_schema_version, updated_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(plugin_id) DO UPDATE SET
              config_json = excluded.config_json,
              updated_at = excluded.updated_at
            """,
            (plugin_id, json.dumps(dict(config), sort_keys=True), self._ts()),
        )


class SqliteCompanyRepository:
    def __init__(self, conn, clock_now):
        self._conn = conn
        self._now = clock_now

    def _ts(self):
        return self._now().strftime("%Y-%m-%dT%H:%M:%SZ")

    async def upsert(self, company_id, canonical_name, **fields):
        normalized = str(fields.get("normalized_name") or canonical_name.lower())
        domains = fields.get("domains", [])
        ats_tenants = fields.get("ats_tenants", [])
        aliases = fields.get("aliases", [])
        notes = fields.get("notes")
        website = fields.get("website")
        cur = await self._conn.execute("SELECT id FROM companies WHERE id = ?", (company_id,))
        if await cur.fetchone() is None:
            await self._conn.execute(
                "INSERT INTO companies(id, canonical_name, normalized_name, domains_json, ats_tenants_json, aliases_json, notes, website, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (company_id, canonical_name, normalized, json.dumps(list(domains) if isinstance(domains, (list, tuple)) else []), json.dumps(list(ats_tenants) if isinstance(ats_tenants, (list, tuple)) else []), json.dumps(list(aliases) if isinstance(aliases, (list, tuple)) else []), notes, website, self._ts()),
            )
        else:
            await self._conn.execute(
                "UPDATE companies SET canonical_name=?, normalized_name=?, domains_json=?, ats_tenants_json=?, aliases_json=?, notes=?, website=? WHERE id=?",
                (canonical_name, normalized, json.dumps(list(domains) if isinstance(domains, (list, tuple)) else []), json.dumps(list(ats_tenants) if isinstance(ats_tenants, (list, tuple)) else []), json.dumps(list(aliases) if isinstance(aliases, (list, tuple)) else []), notes, website, company_id),
            )

    async def get(self, company_id):
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def find_by_name(self, normalized_name):
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute("SELECT id FROM companies WHERE normalized_name = ?", (normalized_name,))
        row = await cur.fetchone()
        return str(row["id"]) if row else None

    async def list(self):
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute("SELECT * FROM companies ORDER BY canonical_name")
        return [dict(r) for r in await cur.fetchall()]


class SqliteJobRepository:
    def __init__(self, conn, clock_now):
        self._conn = conn
        self._now = clock_now

    def _ts(self):
        return self._now().strftime("%Y-%m-%dT%H:%M:%SZ")

    async def upsert(self, job_id, *, company_id, source_id, job, content_hash=None):
        from app.domain.models.job import NormalizedJob

        if isinstance(job, NormalizedJob):
            title = job.title
            normalized_title = job.normalized_title
            title_family = job.title_family
            description_text = job.description_text
            description_html = job.description_html
            locations = [loc.__dict__ for loc in job.locations]
            workplace_type = job.workplace_type.value
            employment_type = job.employment_type.value
            seniority = job.seniority.value
            salary = job.salary.__dict__ if job.salary else None
            posted_at = job.posted_at.strftime("%Y-%m-%dT%H:%M:%SZ") if job.posted_at else None
            source_posting_id = job.source_posting_id
            canonical_url = job.canonical_url
            original_urls = list(job.original_urls)
            department = job.department
            required_skills = list(job.required_skills)
            preferred_skills = list(job.preferred_skills)
            extraction_status = job.extraction_status.value
            extraction_confidence = job.extraction_confidence
            field_confidence = dict(job.field_confidence)
            provenance = [p.__dict__ for p in job.provenance]
            posting_status = job.posting_status_hint.value
            fetched_at = job.fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            title = getattr(job, "title", "")
            normalized_title = str(title).lower()
            title_family = None
            description_text = getattr(job, "description_text", "")
            description_html = None
            locations = []
            workplace_type = getattr(job, "workplace_type", "unknown")
            if hasattr(workplace_type, "value"):
                workplace_type = workplace_type.value
            employment_type = getattr(job, "employment_type", "unknown")
            if hasattr(employment_type, "value"):
                employment_type = employment_type.value
            seniority = getattr(job, "seniority", "unknown")
            if hasattr(seniority, "value"):
                seniority = seniority.value
            salary = None
            posted_at = None
            source_posting_id = None
            canonical_url = getattr(job, "canonical_url", None) or ""
            original_urls = []
            department = None
            required_skills = list(getattr(job, "required_skills", []))
            preferred_skills = list(getattr(job, "preferred_skills", []))
            extraction_status = "complete"
            extraction_confidence = 1.0
            field_confidence = {}
            provenance = []
            posting_status = getattr(job, "posting_status", "active")
            if hasattr(posting_status, "value"):
                posting_status = posting_status.value
            fetched_at = self._ts()

        now = self._ts()
        cur = await self._conn.execute("SELECT id FROM jobs WHERE canonical_url = ?", (canonical_url,))
        existing = await cur.fetchone()
        if existing is None:
            await self._conn.execute(
                """INSERT INTO jobs(id, company_id, title, normalized_title, title_family,
                  description_text, description_html, locations_json, workplace_type,
                  employment_type, seniority, salary_json, posted_at, source_id,
                  source_posting_id, canonical_url, original_urls_json, department,
                  required_skills_json, preferred_skills_json, extraction_status,
                  extraction_confidence, field_confidence_json, provenance_json,
                  posting_status, content_hash, first_seen_at, last_seen_at, fetched_at,
                  created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, company_id, title, normalized_title, title_family, description_text, description_html,
                 json.dumps(locations), workplace_type, employment_type, seniority, json.dumps(salary) if salary else None,
                 posted_at, source_id, source_posting_id, canonical_url, json.dumps(original_urls), department,
                 json.dumps(required_skills), json.dumps(preferred_skills), extraction_status, extraction_confidence,
                 json.dumps(field_confidence), json.dumps(provenance), posting_status, content_hash, now, now, fetched_at, now, now),
            )
        else:
            await self._conn.execute(
                """UPDATE jobs SET company_id=?, title=?, normalized_title=?, title_family=?,
                  description_text=?, description_html=?, locations_json=?, workplace_type=?,
                  employment_type=?, seniority=?, salary_json=?, posted_at=?, source_id=?,
                  source_posting_id=?, original_urls_json=?, department=?,
                  required_skills_json=?, preferred_skills_json=?, extraction_status=?,
                  extraction_confidence=?, field_confidence_json=?, provenance_json=?,
                  posting_status=?, content_hash=?, last_seen_at=?, fetched_at=?, updated_at=?
                WHERE canonical_url=?""",
                (company_id, title, normalized_title, title_family, description_text, description_html,
                 json.dumps(locations), workplace_type, employment_type, seniority, json.dumps(salary) if salary else None,
                 posted_at, source_id, source_posting_id, json.dumps(original_urls), department,
                 json.dumps(required_skills), json.dumps(preferred_skills), extraction_status, extraction_confidence,
                 json.dumps(field_confidence), json.dumps(provenance), posting_status, content_hash, now, fetched_at, now, canonical_url),
            )

    async def get(self, job_id):
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cur.fetchone()
        return self._row(row) if row else None

    async def find_by_canonical_url(self, canonical_url):
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute("SELECT id FROM jobs WHERE canonical_url = ?", (canonical_url,))
        row = await cur.fetchone()
        return str(row["id"]) if row else None

    async def find_by_source_posting(self, source_id, source_posting_id):
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT id FROM jobs WHERE source_id = ? AND source_posting_id = ?",
            (source_id, source_posting_id),
        )
        row = await cur.fetchone()
        return str(row["id"]) if row else None

    async def list(self, *, company_id=None, source_id=None, limit=50, offset=0):
        self._conn.row_factory = aiosqlite.Row
        clauses = []
        params = []
        if company_id:
            clauses.append("company_id = ?")
            params.append(company_id)
        if source_id:
            clauses.append("source_id = ?")
            params.append(source_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([limit, offset])
        cur = await self._conn.execute(f"SELECT * FROM jobs{where} ORDER BY last_seen_at DESC LIMIT ? OFFSET ?", tuple(params))
        return [self._row(r) for r in await cur.fetchall()]

    async def search(self, query, *, limit=50):
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT jobs.* FROM jobs_fts JOIN jobs ON jobs.rowid = jobs_fts.rowid WHERE jobs_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        )
        return [self._row(r) for r in await cur.fetchall()]

    def _row(self, row: aiosqlite.Row) -> dict[str, object]:
        """Return the job with its JSON columns decoded."""
        return {
            "id": row["id"],
            "company_id": row["company_id"],
            "title": row["title"],
            "normalized_title": row["normalized_title"],
            "title_family": row["title_family"],
            "description_text": row["description_text"],
            "description_html": row["description_html"],
            "locations": json.loads(row["locations_json"] or "[]"),
            "workplace_type": row["workplace_type"],
            "employment_type": row["employment_type"],
            "seniority": row["seniority"],
            "salary": json.loads(row["salary_json"]) if row["salary_json"] else None,
            "posted_at": row["posted_at"],
            "source_id": row["source_id"],
            "source_posting_id": row["source_posting_id"],
            "canonical_url": row["canonical_url"],
            "original_urls": json.loads(row["original_urls_json"] or "[]"),
            "department": row["department"],
            "required_skills": json.loads(row["required_skills_json"] or "[]"),
            "preferred_skills": json.loads(row["preferred_skills_json"] or "[]"),
            "extraction_status": row["extraction_status"],
            "extraction_confidence": row["extraction_confidence"],
            "field_confidence": json.loads(row["field_confidence_json"] or "{}"),
            "provenance": json.loads(row["provenance_json"] or "[]"),
            "posting_status": row["posting_status"],
            "content_hash": row["content_hash"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "fetched_at": row["fetched_at"],
        }

    async def count(self):
        cur = await self._conn.execute("SELECT COUNT(*) FROM jobs")
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def mark_not_seen(self, source_id, seen_ids, cutoff):
        ts = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        if not seen_ids:
            cur = await self._conn.execute("UPDATE jobs SET posting_status='removed', last_seen_at=? WHERE source_id=?", (ts, source_id))
            return cur.rowcount or 0
        placeholders = ",".join("?" for _ in seen_ids)
        params = [ts, source_id, *seen_ids]
        cur = await self._conn.execute(f"UPDATE jobs SET posting_status='removed', last_seen_at=? WHERE source_id=? AND id NOT IN ({placeholders})", tuple(params))
        return cur.rowcount or 0


class SqliteObservationRepository:
    def __init__(self, conn, clock_now):
        self._conn = conn
        self._now = clock_now

    async def add(self, *, job_id, source_id, observed_at, posting_status, details=None):
        await self._conn.execute(
            "INSERT INTO job_observations(id, job_id, source_id, observed_at, posting_status, details_json) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), job_id, source_id, observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"), posting_status, json.dumps(dict(details or {}), sort_keys=True)),
        )

    async def list_for_job(self, job_id):
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute("SELECT * FROM job_observations WHERE job_id = ? ORDER BY observed_at DESC", (job_id,))
        return [dict(r) for r in await cur.fetchall()]


class SqliteCrawlRunRepository:
    def __init__(self, conn, clock_now):
        self._conn = conn
        self._now = clock_now

    def _ts(self):
        return self._now().strftime("%Y-%m-%dT%H:%M:%SZ")

    async def create(self, run_id, source_id, started_at):
        await self._conn.execute(
            "INSERT INTO crawl_runs(id, source_id, status, started_at, stats_json) VALUES (?,?,?,?,?)",
            (run_id, source_id, "running", started_at.strftime("%Y-%m-%dT%H:%M:%SZ"), "{}"),
        )

    async def finish(self, run_id, *, status, finished_at, stats, error=None):
        await self._conn.execute(
            "UPDATE crawl_runs SET status=?, finished_at=?, stats_json=?, error=? WHERE id=?",
            (status, finished_at.strftime("%Y-%m-%dT%H:%M:%SZ"), json.dumps(dict(stats)), error, run_id),
        )

    async def get(self, run_id):
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute("SELECT * FROM crawl_runs WHERE id = ?", (run_id,))
        row = await cur.fetchone()
        return self._row(row) if row else None

    async def list(self, *, source_id=None, limit=50):
        self._conn.row_factory = aiosqlite.Row
        if source_id:
            cur = await self._conn.execute("SELECT * FROM crawl_runs WHERE source_id = ? ORDER BY started_at DESC LIMIT ?", (source_id, limit))
        else:
            cur = await self._conn.execute("SELECT * FROM crawl_runs ORDER BY started_at DESC LIMIT ?", (limit,))
        return [self._row(r) for r in await cur.fetchall()]

    async def mark_interrupted(self) -> int:
        """Close crawl runs that were running when the worker stopped."""
        cur = await self._conn.execute(
            "UPDATE crawl_runs SET status = 'interrupted', finished_at = ? WHERE status = 'running'",
            (self._now().strftime("%Y-%m-%dT%H:%M:%SZ"),),
        )
        return cur.rowcount or 0

    def _row(self, row: aiosqlite.Row) -> dict[str, object]:
        """Return the run with decoded stats (the column is ``stats_json``)."""
        return {
            "id": row["id"],
            "source_id": row["source_id"],
            "status": row["status"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "stats": json.loads(row["stats_json"] or "{}"),
            "error": row["error"],
        }


# ---------------------------------------------------------------------------
# M1 discovery repositories
# ---------------------------------------------------------------------------


class SqliteSourceRepository:
    def __init__(self, conn: aiosqlite.Connection, clock_now: Any) -> None:
        self._conn = conn
        self._now = clock_now

    def _ts(self) -> str:
        return self._now().strftime("%Y-%m-%dT%H:%M:%SZ")

    async def add(self, source: SourceConfig) -> None:
        await self._conn.execute(
            """
            INSERT INTO sources(id, url, kind, company_id, connector_id, ats_tenant_key,
              enabled, extraction_order, limits_json, schedule_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source.id,
                source.url,
                source.kind,
                source.company_id,
                source.connector_id,
                source.ats_tenant_key,
                1 if source.enabled else 0,
                json.dumps(list(source.extraction_order)),
                json.dumps(dict(source.limits)),
                json.dumps(dict(source.schedule)),
                self._ts(),
                self._ts(),
            ),
        )

    async def get(self, source_id: str) -> SourceConfig | None:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        return self._row(row)

    async def list(self, *, enabled_only: bool = False) -> Sequence[SourceConfig]:
        self._conn.row_factory = aiosqlite.Row
        if enabled_only:
            cur = await self._conn.execute("SELECT * FROM sources WHERE enabled = 1")
        else:
            cur = await self._conn.execute("SELECT * FROM sources")
        return [self._row(r) for r in await cur.fetchall()]

    async def set_enabled(self, source_id: str, enabled: bool) -> None:
        await self._conn.execute(
            "UPDATE sources SET enabled = ?, updated_at = ? WHERE id = ?",
            (1 if enabled else 0, self._ts(), source_id),
        )

    async def save(self, source: SourceConfig) -> None:
        await self._conn.execute(
            """
            UPDATE sources SET url=?, kind=?, company_id=?, connector_id=?, ats_tenant_key=?,
              enabled=?, extraction_order=?, limits_json=?, schedule_json=?, updated_at=?
            WHERE id=?
            """,
            (
                source.url,
                source.kind,
                source.company_id,
                source.connector_id,
                source.ats_tenant_key,
                1 if source.enabled else 0,
                json.dumps(list(source.extraction_order)),
                json.dumps(dict(source.limits)),
                json.dumps(dict(source.schedule)),
                self._ts(),
                source.id,
            ),
        )

    async def delete(self, source_id: str) -> None:
        await self._conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))

    def _row(self, row: aiosqlite.Row) -> SourceConfig:
        return SourceConfig(
            id=row["id"],
            url=row["url"],
            kind=row["kind"],
            company_id=row["company_id"],
            connector_id=row["connector_id"],
            ats_tenant_key=row["ats_tenant_key"],
            enabled=bool(row["enabled"]),
            extraction_order=tuple(json.loads(row["extraction_order"] or "[]")),
            limits=json.loads(row["limits_json"] or "{}"),
            schedule=json.loads(row["schedule_json"] or "{}"),
        )


class SqliteSnapshotRepository:
    """Append-only snapshots: one row per observed content change."""

    def __init__(self, conn, clock_now):
        self._conn = conn
        self._now = clock_now

    async def add(self, *, job_id, content_hash, snapshot, created_at):
        await self._conn.execute(
            "INSERT INTO job_snapshots(id, job_id, content_hash, snapshot_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                f"snap_{uuid.uuid4().hex[:12]}",
                job_id,
                content_hash,
                json.dumps(dict(snapshot), sort_keys=True, default=str),
                created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )

    async def list_for_job(self, job_id):
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM job_snapshots WHERE job_id = ? ORDER BY created_at DESC",
            (job_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


class SqliteAliasRepository:
    """Alternative URLs that point at the same job."""

    def __init__(self, conn, clock_now):
        self._conn = conn
        self._now = clock_now

    async def add(self, *, job_id, alias_url, alias_source_id, created_at):
        await self._conn.execute(
            "INSERT OR IGNORE INTO job_aliases(id, job_id, alias_url, alias_source_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                f"alias_{uuid.uuid4().hex[:12]}",
                job_id,
                alias_url,
                alias_source_id,
                created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )

    async def find_by_url(self, alias_url):
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT job_id FROM job_aliases WHERE alias_url = ?", (alias_url,)
        )
        row = await cur.fetchone()
        return str(row["job_id"]) if row else None

    async def list_for_job(self, job_id):
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM job_aliases WHERE job_id = ? ORDER BY created_at DESC", (job_id,)
        )
        return [dict(r) for r in await cur.fetchall()]
