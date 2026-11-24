"""Ports for local backup archives and their audit trail (M11)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.domain.models.backup import BackupRunRecord


class BackupRunRepository(Protocol):
    async def add(self, record: BackupRunRecord) -> None: ...

    async def list_runs(self, *, limit: int = 20) -> Sequence[Mapping[str, object]]: ...


class BackupStore(Protocol):
    """Reads and writes the portable archive for the local database.

    Import is only defined for an empty database. Append-only history tables
    refuse deletes by trigger, so replacing an existing database in place is
    deliberately not offered; the service checks emptiness before calling.
    """

    async def schema_version(self) -> int: ...

    async def export_tables(
        self,
    ) -> tuple[Mapping[str, Mapping[str, Sequence[Any]]], int]:
        """All user tables with their column lists and rows, plus the version."""
        ...

    async def write_archive(self, archive: Mapping[str, Any], path: str) -> str:
        """Write an archive to disk and return the resolved path."""
        ...

    async def read_archive(self, path: str) -> dict[str, Any]: ...

    async def table_counts(self) -> Mapping[str, int]: ...

    async def import_archive(
        self, tables: Mapping[str, Mapping[str, Sequence[Any]]]
    ) -> Mapping[str, int]:
        """Insert every archived row; returns the counts written."""
        ...
