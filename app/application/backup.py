"""Local backup export/import (M11).

A backup is a portable, checksummed JSON archive of the user's local tables.
Import only fills an **empty** database and needs an explicit ``confirm``; it
never overwrites existing rows, because the append-only history tables refuse
deletes by trigger and a silent replace would destroy evidence. Every export and
import appends an audit row, and an import verifies row counts before reporting
success.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.application.config import live_config
from app.domain.models.backup import BackupRunRecord
from app.domain.models.common import PortError
from app.domain.services.backup_archive import (
    ArchiveError,
    build_archive,
    validate_archive,
)

#: Settings key remembering the last automatic backup.
SCHEDULED_BACKUP_KEY = "last_scheduled_backup_at"


class BackupService:
    def __init__(self, container: Any, store: Any = None) -> None:
        self._container = container
        self._store = (
            store
            or getattr(container, "backup_store", None)
            or container.extra.get("backup_store")
        )

    def _require_store(self) -> Any:
        if self._store is None:
            raise PortError(
                "plugin.unavailable",
                "backup needs the SQLite data store, which is not configured",
            )
        return self._store

    async def export(self, *, path: str | None = None) -> dict[str, Any]:
        store = self._require_store()
        now = self._container.clock.now()
        tables, schema_version = await store.export_tables()
        archive = build_archive(
            tables,
            schema_version=schema_version,
            app_version=self._container.app_version,
            created_at=now.isoformat(),
        )
        target = path or f"backups/jobsignal-backup-{_stamp(now)}.json"
        resolved = await store.write_archive(archive, target)
        await self._record(
            kind="export",
            path=resolved,
            checksum=str(archive["checksum"]),
            schema_version=schema_version,
            counts=archive["counts"],
            detail={},
            now=now,
        )
        return {
            "ok": True,
            "path": resolved,
            "checksum": archive["checksum"],
            "counts": archive["counts"],
            "schema_version": schema_version,
            "created_at": now.isoformat(),
        }

    async def run_if_due(self) -> dict[str, Any] | None:
        """The scheduled-backup policy: disabled, interval, state, prune.

        The first check records a baseline instead of archiving whatever the
        install happens to contain; the first automatic archive is due one
        interval later.
        """
        config = live_config(self._container).backup
        if not config.enabled:
            return None
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            raw = await uow.settings.get(SCHEDULED_BACKUP_KEY)
            await uow.commit()
        last: datetime | None = None
        stamp = raw.get("at") if isinstance(raw, dict) else None
        if isinstance(stamp, str) and stamp:
            try:
                last = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                last = None
        if last is None:
            await self._record_scheduled_at(now)
            return None
        if now - last < timedelta(days=config.interval_days):
            return None
        result = await self.scheduled()
        await self._record_scheduled_at(now)
        return result

    async def _record_scheduled_at(self, now: datetime) -> None:
        async with self._container.uow_factory() as uow:
            await uow.settings.put(
                SCHEDULED_BACKUP_KEY, {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
            )
            await uow.commit()

    async def scheduled(self) -> dict[str, Any]:
        """Write one automatic archive, then keep only the newest ``keep``.

        Retention is scoped to ``jobsignal-auto-*`` names, so a manual export
        is never deleted by the scheduler.
        """
        config = live_config(self._container).backup
        now = self._container.clock.now()
        result = await self.export(
            path=f"backups/jobsignal-auto-{_stamp(now)}.json"
        )
        store = self._require_store()
        archives = store.auto_archives()
        for stale in archives[: max(0, len(archives) - int(config.keep))]:
            store.delete_archive(stale)
        return result

    async def import_archive(
        self, *, path: str, confirm: bool = False
    ) -> dict[str, Any]:
        store = self._require_store()
        if not confirm:
            raise PortError(
                "validation.confirmation_required",
                "importing replaces nothing until you confirm; pass confirm = true",
            )
        now = self._container.clock.now()
        raw = await store.read_archive(path)
        schema_version = await store.schema_version()
        try:
            archive = validate_archive(raw, schema_version=schema_version)
        except ArchiveError as exc:
            raise PortError("validation.invalid_backup", str(exc)) from exc

        existing = dict(await store.table_counts())
        occupied = {name: count for name, count in existing.items() if count}
        if occupied:
            raise PortError(
                "conflict.backup_not_empty",
                (
                    "this database already holds data; import only fills an empty "
                    "database so nothing is overwritten"
                ),
                details={"table_counts": occupied},
            )

        written = dict(await store.import_archive(archive["tables"]))
        after = dict(await store.table_counts())
        expected = {
            name: len(table["rows"]) for name, table in archive["tables"].items()
        }
        mismatched = {
            name: {"expected": count, "actual": after.get(name)}
            for name, count in expected.items()
            if after.get(name) != count
        }
        if mismatched:
            raise PortError(
                "internal.backup_verification_failed",
                "the restored row counts did not match the backup",
                retryable=False,
                details={"tables": mismatched},
            )
        await self._record(
            kind="import",
            path=path,
            checksum=str(archive["checksum"]),
            schema_version=schema_version,
            counts=written,
            detail={"verified": True, "backup_created_at": archive["created_at"]},
            now=now,
        )
        return {
            "ok": True,
            "verified": True,
            "path": path,
            "checksum": archive["checksum"],
            "counts": written,
            "schema_version": schema_version,
            "imported_at": now.isoformat(),
        }

    async def history(self, *, limit: int = 20) -> dict[str, Any]:
        async with self._container.uow_factory() as uow:
            runs = [dict(row) for row in await uow.backups.list_runs(limit=limit)]
            await uow.commit()
        return {"runs": runs, "note": "Exports and imports are recorded append-only."}

    async def _record(
        self,
        *,
        kind: str,
        path: str,
        checksum: str,
        schema_version: int,
        counts: Any,
        detail: dict[str, Any],
        now: datetime,
    ) -> None:
        async with self._container.uow_factory() as uow:
            await uow.backups.add(
                BackupRunRecord(
                    id=f"br_{self._container.ids.new_id()[:16]}",
                    kind=kind,
                    path=path,
                    checksum=checksum,
                    schema_version=schema_version,
                    app_version=self._container.app_version,
                    created_at=now,
                    table_counts={str(key): int(value) for key, value in dict(counts).items()},
                    detail=detail,
                )
            )
            await uow.commit()


def _stamp(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ")


__all__ = ["BackupService"]
