"""Backup archive format, shared by the SQLite and memory stores.

An archive is a JSON object with a per-table column list and row values. It is
portable, checksummed, and versioned; importing it is an explicit, all-or-nothing
operation that verifies the row counts afterwards.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

FORMAT = "jobsignal.backup/1"
#: Tables that belong to the app's internals rather than the user's data, or that
#: are rebuilt by SQLite/triggers and must never be written directly.
EXCLUDED_TABLES = frozenset({"schema_migrations", "backup_runs"})
#: FTS shadow tables follow ``<name>_data``/``_idx``/``_content``/``_docsize``/
#: ``_config`` and are managed by the virtual table.
FTS_SUFFIXES = ("_data", "_idx", "_content", "_docsize", "_config", "_segments", "_segdir", "_stat")


def is_backup_table(name: str) -> bool:
    lowered = name.lower()
    if lowered.startswith("sqlite_") or lowered in EXCLUDED_TABLES:
        return False
    if "jobs_fts" in lowered:
        return False
    if any(lowered.endswith(suffix) for suffix in FTS_SUFFIXES):
        return False
    return True


def canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def archive_checksum(
    tables: Mapping[str, Mapping[str, Sequence[Any]]], *, schema_version: int
) -> str:
    body = {
        "format": FORMAT,
        "schema_version": int(schema_version),
        "tables": {
            name: {
                "columns": list(table["columns"]),
                "rows": [list(row) for row in table["rows"]],
            }
            for name, table in sorted(tables.items())
        },
    }
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def build_archive(
    tables: Mapping[str, Mapping[str, Sequence[Any]]],
    *,
    schema_version: int,
    app_version: str,
    created_at: str,
) -> dict[str, Any]:
    normalized = {
        name: {
            "columns": list(table["columns"]),
            "rows": [list(row) for row in table["rows"]],
        }
        for name, table in sorted(tables.items())
    }
    counts = {name: len(table["rows"]) for name, table in normalized.items()}
    return {
        "format": FORMAT,
        "schema_version": int(schema_version),
        "app_version": app_version,
        "created_at": created_at,
        "tables": normalized,
        "counts": counts,
        "checksum": archive_checksum(normalized, schema_version=schema_version),
    }


class ArchiveError(Exception):
    """The archive is malformed, checksummed wrong, or from another schema."""


def validate_archive(archive: object, *, schema_version: int) -> dict[str, Any]:
    if not isinstance(archive, dict):
        raise ArchiveError("backup is not an object")
    if archive.get("format") != FORMAT:
        raise ArchiveError("unrecognised backup format")
    try:
        stored_version = int(str(archive.get("schema_version")))
    except (TypeError, ValueError) as exc:
        raise ArchiveError("backup has no schema version") from exc
    if stored_version != int(schema_version):
        raise ArchiveError(
            f"backup schema version {stored_version} does not match {schema_version}"
        )
    tables = archive.get("tables")
    if not isinstance(tables, dict):
        raise ArchiveError("backup has no tables")
    normalized: dict[str, dict[str, Any]] = {}
    for name, table in tables.items():
        if not is_backup_table(str(name)):
            raise ArchiveError(f"backup contains a forbidden table: {name}")
        if not isinstance(table, dict):
            raise ArchiveError(f"table {name} is malformed")
        columns = table.get("columns")
        rows = table.get("rows")
        if not isinstance(columns, list) or not isinstance(rows, list):
            raise ArchiveError(f"table {name} is malformed")
        normalized[str(name)] = {
            "columns": [str(column) for column in columns],
            "rows": [list(row) for row in rows],
        }
    expected = archive_checksum(normalized, schema_version=stored_version)
    if expected != archive.get("checksum"):
        raise ArchiveError("backup checksum does not match its contents")
    counts = archive.get("counts")
    if not isinstance(counts, dict):
        raise ArchiveError("backup has no counts")
    for name, table in normalized.items():
        if int(counts.get(name, -1)) != len(table["rows"]):
            raise ArchiveError(f"backup count for {name} does not match its rows")
    return {
        "format": FORMAT,
        "schema_version": stored_version,
        "app_version": str(archive.get("app_version") or ""),
        "created_at": str(archive.get("created_at") or ""),
        "tables": normalized,
        "counts": {name: len(table["rows"]) for name, table in normalized.items()},
        "checksum": str(archive.get("checksum")),
    }


__all__ = [
    "FORMAT",
    "ArchiveError",
    "archive_checksum",
    "build_archive",
    "canonical_bytes",
    "is_backup_table",
    "validate_archive",
]
