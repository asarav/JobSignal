from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from app.domain.models.enums import TaskStatus
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
