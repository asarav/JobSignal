"""Archive format rules: checksum, version, tables, counts."""

from __future__ import annotations

import pytest
from app.domain.services.backup_archive import (
    ArchiveError,
    archive_checksum,
    build_archive,
    is_backup_table,
    validate_archive,
)


def _archive() -> dict:
    return build_archive(
        {"companies": {"columns": ["id", "name"], "rows": [["co_a", "Acme"]]}},
        schema_version=18,
        app_version="0.1.0",
        created_at="2026-03-01T00:00:00Z",
    )


def test_an_archive_round_trips() -> None:
    archive = _archive()
    checked = validate_archive(archive, schema_version=18)

    assert checked["counts"] == {"companies": 1}
    assert checked["tables"]["companies"]["rows"] == [["co_a", "Acme"]]
    assert checked["checksum"] == archive["checksum"]


def test_a_tampered_row_is_rejected() -> None:
    archive = _archive()
    archive["tables"]["companies"]["rows"].append(["co_b", "Other"])

    with pytest.raises(ArchiveError, match="checksum"):
        validate_archive(archive, schema_version=18)


def test_a_mismatched_schema_is_rejected() -> None:
    with pytest.raises(ArchiveError, match="schema version"):
        validate_archive(_archive(), schema_version=17)


def test_an_internal_table_is_rejected() -> None:
    archive = _archive()
    archive["tables"]["schema_migrations"] = {"columns": ["version"], "rows": [[1]]}
    archive["counts"]["schema_migrations"] = 1
    archive["checksum"] = archive_checksum(
        archive["tables"], schema_version=archive["schema_version"]
    )

    with pytest.raises(ArchiveError, match="forbidden"):
        validate_archive(archive, schema_version=18)


def test_internal_and_fts_tables_are_not_backed_up() -> None:
    assert not is_backup_table("schema_migrations")
    assert not is_backup_table("backup_runs")
    assert not is_backup_table("jobs_fts_data")
    assert not is_backup_table("sqlite_sequence")
    assert is_backup_table("jobs")
    assert is_backup_table("applications")
