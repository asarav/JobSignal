"""SQLite backup archive reader/writer and its append-only audit repository."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from app.domain.models.backup import BackupRunRecord
from app.domain.models.common import PortError
from app.domain.services.backup_archive import is_backup_table

#: A local backup should never be an unbounded read.
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024


def _row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["table_counts"] = json.loads(data.pop("table_counts_json") or "{}")
    data["detail"] = json.loads(data.pop("detail_json") or "{}")
    return data


class SqliteBackupRunRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(self, record: BackupRunRecord) -> None:
        await self._conn.execute(
            """
            INSERT INTO backup_runs(
              id, kind, path, checksum, schema_version, app_version,
              table_counts_json, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.kind,
                record.path,
                record.checksum,
                record.schema_version,
                record.app_version,
                json.dumps(record.table_counts, sort_keys=True),
                json.dumps(record.detail, sort_keys=True),
                record.created_at.isoformat(),
            ),
        )

    async def list_runs(self, *, limit: int = 20) -> Sequence[Mapping[str, object]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM backup_runs ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        return [_row(row) for row in await cur.fetchall()]


class SqliteBackupStore:
    """Portable JSON archive over the local SQLite database."""

    def __init__(self, db_path: Path, data_dir: Path | str = "") -> None:
        self._db_path = Path(db_path)
        self._data_dir = Path(data_dir) if data_dir else self._db_path.parent

    async def schema_version(self) -> int:
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute("SELECT MAX(version) FROM schema_migrations")
            row = await cur.fetchone()
            return int(row[0] or 0) if row else 0

    async def _table_names(self, conn: aiosqlite.Connection) -> list[str]:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        return [
            str(row[0])
            for row in await cur.fetchall()
            if is_backup_table(str(row[0]))
        ]

    async def export_tables(
        self,
    ) -> tuple[Mapping[str, Mapping[str, Sequence[Any]]], int]:
        async with aiosqlite.connect(self._db_path) as conn:
            version = 0
            cur = await conn.execute("SELECT MAX(version) FROM schema_migrations")
            row = await cur.fetchone()
            if row:
                version = int(row[0] or 0)
            tables: dict[str, dict[str, Any]] = {}
            for name in await self._table_names(conn):
                cur = await conn.execute(f'SELECT * FROM "{name}"')
                columns = [str(column[0]) for column in cur.description or ()]
                rows = [list(row) for row in await cur.fetchall()]
                tables[name] = {"columns": columns, "rows": rows}
            return tables, version

    def resolve_path(self, path: str) -> Path:
        base = (self._data_dir / "backups").resolve()
        candidate = Path(path)
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (self._data_dir / candidate).resolve()
        )
        if resolved != base and base not in resolved.parents:
            raise PortError(
                "validation.invalid_params",
                "backup paths must stay inside the data directory",
            )
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    def auto_archives(self) -> list[Path]:
        """Scheduled archives only, oldest first; manual exports are untouched."""
        folder = (self._data_dir / "backups").resolve()
        if not folder.is_dir():
            return []
        return sorted(folder.glob("jobsignal-auto-*.json"), key=lambda item: item.name)

    def delete_archive(self, path: Path | str) -> None:
        target = self.resolve_path(str(path))
        target.unlink(missing_ok=True)

    async def write_archive(self, archive: Mapping[str, Any], path: str) -> str:
        target = self.resolve_path(path)
        target.write_text(
            json.dumps(archive, sort_keys=True, indent=2), encoding="utf-8"
        )
        return str(target)

    async def read_archive(self, path: str) -> dict[str, Any]:
        source = self.resolve_path(path)
        if not source.exists():
            raise PortError("not_found", f"backup file not found: {source.name}")
        if source.stat().st_size > MAX_ARCHIVE_BYTES:
            raise PortError(
                "validation.invalid_params", "backup file is too large to import"
            )
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise PortError(
                "validation.invalid_params", "backup file is not valid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise PortError("validation.invalid_params", "backup file is malformed")
        return payload

    async def table_counts(self) -> Mapping[str, int]:
        async with aiosqlite.connect(self._db_path) as conn:
            counts: dict[str, int] = {}
            for name in await self._table_names(conn):
                cur = await conn.execute(f'SELECT COUNT(*) FROM "{name}"')
                row = await cur.fetchone()
                counts[name] = int(row[0]) if row else 0
            return counts

    async def import_archive(
        self, tables: Mapping[str, Mapping[str, Sequence[Any]]]
    ) -> Mapping[str, int]:
        written: dict[str, int] = {}
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("PRAGMA foreign_keys = OFF")
            await conn.execute("BEGIN IMMEDIATE")
            try:
                known = set(await self._table_names(conn))
                for name in sorted(tables):
                    if name not in known:
                        raise PortError(
                            "validation.invalid_params",
                            f"backup table {name} does not exist in this database",
                        )
                    table = tables[name]
                    columns = [str(column) for column in table["columns"]]
                    placeholders = ",".join("?" for _ in columns)
                    column_sql = ",".join(f'"{column}"' for column in columns)
                    rows = [list(row) for row in table["rows"]]
                    await conn.executemany(
                        f'INSERT INTO "{name}" ({column_sql}) VALUES ({placeholders})',
                        rows,
                    )
                    written[name] = len(rows)
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
            finally:
                await conn.execute("PRAGMA foreign_keys = ON")
        return written


def timestamp(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ")


__all__ = ["SqliteBackupRunRepository", "SqliteBackupStore"]
