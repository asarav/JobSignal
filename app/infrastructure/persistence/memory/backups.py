"""In-memory twins for the backup audit repository and archive store."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.domain.models.backup import BackupRunRecord
from app.domain.models.common import PortError


class InMemoryBackupRunRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def add(self, record: BackupRunRecord) -> None:
        self.records.append(
            {
                "id": record.id,
                "kind": record.kind,
                "path": record.path,
                "checksum": record.checksum,
                "schema_version": record.schema_version,
                "app_version": record.app_version,
                "table_counts": dict(record.table_counts),
                "detail": dict(record.detail),
                "created_at": record.created_at.isoformat(),
            }
        )

    async def list_runs(self, *, limit: int = 20) -> Sequence[Mapping[str, object]]:
        rows = [dict(row) for row in self.records]
        rows.sort(key=lambda row: str(row["created_at"]), reverse=True)
        return rows[:limit]


class InMemoryBackupStore:
    """Archive store over a plain table mapping, mirroring the SQLite rules.

    The real adapter reads the SQLite file; this twin is used by tests and the
    memory composition so the export/import/verify flow is exercisable without a
    database. ``tables`` can be seeded directly in tests.
    """

    def __init__(
        self, tables: Mapping[str, Mapping[str, Sequence[Any]]] | None = None, data_dir: str = ""
    ) -> None:
        self.tables: dict[str, dict[str, Any]] = {
            name: {
                "columns": [str(column) for column in table["columns"]],
                "rows": [list(row) for row in table["rows"]],
            }
            for name, table in (tables or {}).items()
        }
        self._data_dir = Path(data_dir or "backup-test-data")

    async def schema_version(self) -> int:
        return 18

    async def export_tables(
        self,
    ) -> tuple[Mapping[str, Mapping[str, Sequence[Any]]], int]:
        return self.tables, await self.schema_version()

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
        folder = (self._data_dir / "backups").resolve()
        if not folder.is_dir():
            return []
        return sorted(folder.glob("jobsignal-auto-*.json"), key=lambda item: item.name)

    def delete_archive(self, path: Path | str) -> None:
        target = self.resolve_path(str(path))
        target.unlink(missing_ok=True)

    async def write_archive(self, archive: Mapping[str, Any], path: str) -> str:
        target = self.resolve_path(path)
        target.write_text(json.dumps(archive, sort_keys=True), encoding="utf-8")
        return str(target)

    async def read_archive(self, path: str) -> dict[str, Any]:
        source = self.resolve_path(path)
        if not source.exists():
            raise PortError("not_found", f"backup file not found: {source.name}")
        return json.loads(source.read_text(encoding="utf-8"))

    async def table_counts(self) -> Mapping[str, int]:
        return {name: len(table["rows"]) for name, table in self.tables.items()}

    async def import_archive(
        self, tables: Mapping[str, Mapping[str, Sequence[Any]]]
    ) -> Mapping[str, int]:
        written: dict[str, int] = {}
        for name, table in tables.items():
            columns = [str(column) for column in table["columns"]]
            rows = [list(row) for row in table["rows"]]
            self.tables[name] = {"columns": columns, "rows": rows}
            written[name] = len(rows)
        return written
