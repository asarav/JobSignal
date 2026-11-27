"""Adopt the database left by a previous installation, then remove it.

Upgrading in place needs no transfer: the worker database lives in the OS app
data directory, which is keyed by the bundle identifier, so an installer
replaces binaries and leaves the database where it was. The cases that lose
data are the ones where that key moves -- a build that shipped under an older
identifier, or a reinstall that starts in a fresh directory while the previous
one is still on disk. This module copies such a database into place before the
worker opens it.

Once the copy is in place and verified -- integrity, a Job Signal schema this
build understands, and matching row counts for the core tables -- the previous
directory is removed. Two live copies of a local database are how "my old jobs
came back" bugs start, and the user asked the new install to take the data over
rather than to keep a duplicate.

Every step is guarded. A damaged file, a foreign database or one from a newer
build is refused and left exactly where it is. If the copy cannot be verified,
or the old folder cannot be deleted (a locked file, a permission problem), the
folder stays and startup continues: losing the rows is worse than a duplicate.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.infrastructure.persistence.sqlite.migrator import (
    list_migration_files,
)

#: Bundle identifier the current build writes under.
CURRENT_IDENTIFIER = "com.jobsignal.desktop"

#: Identifiers the desktop app has shipped under before.
PREVIOUS_IDENTIFIERS: tuple[str, ...] = ("com.jobsignal.app",)

DB_NAME = "lodestar.sqlite"
WORKER_SUBDIR = "worker"
RECORD_NAME = "adoption.json"
STAGING_DIR = ".adoption-staging"

#: An explicit data directory is authoritative; never adopt into it.
ENV_OVERRIDE = "LODESTAR_DATA_DIR"

_SQLITE_SIDECARS = ("-wal", "-shm")

#: Tables compared between the source and the copy before the source is removed.
#: An older schema may not have them all; only the ones present on both sides
#: are compared, so a pre-profile install still cleans up after itself.
CORE_TABLES = (
    "companies",
    "jobs",
    "candidate_profiles",
    "resume_versions",
    "applications",
)


@dataclass(frozen=True)
class AdoptionResult:
    """What happened when the worker looked for a previous install."""

    adopted: bool
    reason: str
    source: Path | None = None
    schema_version: int | None = None
    removed: bool = False


@dataclass(frozen=True)
class _Candidate:
    """A previous install's database plus the folder that owns it."""

    database: Path
    folder: Path
    owner: Path
    identifier: str


def app_data_roots(home: Path | None = None) -> list[Path]:
    """The per-user application-data root for this platform.

    ``home`` exists so the layout of a platform that is not the current one can
    be pinned by a test; callers pass nothing and get this user's home.
    """
    home = home or Path.home()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return [Path(base)]
        return [home / "AppData" / "Roaming"]
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support"]
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return [Path(base)]
    return [home / ".local" / "share"]


def installed_data_dir(
    roots: Sequence[Path] | None = None,
    identifier: str = CURRENT_IDENTIFIER,
) -> Path:
    """Where the packaged app keeps its worker data on this platform."""
    root = Path((roots or app_data_roots())[0])
    return (root / identifier / WORKER_SUBDIR).resolve()


def shipped_schema_version() -> int:
    """Schema version this build migrates to."""
    return max(
        (int(path.name.split("_", 1)[0]) for path in list_migration_files()),
        default=0,
    )


def adopt_previous_install(
    data_dir: Path,
    *,
    roots: Sequence[Path] | None = None,
    identifiers: Sequence[str] = PREVIOUS_IDENTIFIERS,
    environ: Mapping[str, str] | None = None,
) -> AdoptionResult:
    """Take over a previous install's database in an empty ``data_dir``.

    Refuses to touch anything when the data directory already holds a database,
    when ``LODESTAR_DATA_DIR`` is set, or when ``data_dir`` is not the packaged
    app's own directory (development and test data directories live elsewhere).
    """
    env = os.environ if environ is None else environ
    resolved = Path(data_dir).resolve()
    if env.get(ENV_OVERRIDE):
        return AdoptionResult(
            False, f"{ENV_OVERRIDE} is set; that directory is authoritative"
        )
    roots = [Path(root) for root in (roots or app_data_roots())]
    if resolved != installed_data_dir(roots):
        return AdoptionResult(False, "not the installed app's data directory")
    if (resolved / DB_NAME).exists():
        return AdoptionResult(False, "the data directory already has a database")

    candidates = _candidates(roots, identifiers, resolved)
    if not candidates:
        return AdoptionResult(False, "no previous installation was found")
    reason = "no previous installation was found"
    for candidate in candidates:
        result = _adopt(candidate, resolved)
        if result.adopted:
            return result
        reason = result.reason
    return AdoptionResult(False, reason)


def _candidates(
    roots: Sequence[Path],
    identifiers: Sequence[str],
    data_dir: Path,
) -> list[_Candidate]:
    """Previous databases on disk, most recently written first."""
    found: dict[Path, _Candidate] = {}
    for root in roots:
        for identifier in identifiers:
            owner = (root / identifier).resolve()
            for folder in (owner / WORKER_SUBDIR, owner):
                database = folder / DB_NAME
                if not database.is_file():
                    continue
                resolved = folder.resolve()
                # Never look outside <root>/<identifier>; that is the only
                # directory this module is allowed to adopt or delete.
                if resolved != owner and resolved.parent != owner:
                    continue
                if (
                    resolved == data_dir
                    or resolved in data_dir.parents
                    or data_dir in resolved.parents
                ):
                    continue
                found.setdefault(
                    resolved,
                    _Candidate(
                        database=database,
                        folder=resolved,
                        owner=owner,
                        identifier=identifier,
                    ),
                )
    return sorted(
        found.values(),
        key=lambda candidate: candidate.database.stat().st_mtime,
        reverse=True,
    )


def _adopt(candidate: _Candidate, data_dir: Path) -> AdoptionResult:
    stage = data_dir / STAGING_DIR
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    staged = stage / DB_NAME
    try:
        shutil.copy2(candidate.database, staged)
        for suffix in _SQLITE_SIDECARS:
            sidecar = Path(f"{candidate.database}{suffix}")
            if sidecar.is_file():
                shutil.copy2(sidecar, Path(f"{staged}{suffix}"))
        ok, version, detail = _inspect(staged)
        if not ok:
            return AdoptionResult(False, f"{candidate.database} {detail}")
        shipped = shipped_schema_version()
        if version > shipped:
            return AdoptionResult(
                False,
                f"{candidate.database} was written by a newer build "
                f"(schema {version}, this build ships {shipped})",
            )
        before = _core_counts(candidate.database)
        target = data_dir / DB_NAME
        for suffix in ("", *_SQLITE_SIDECARS):
            part = Path(f"{staged}{suffix}")
            if part.is_file():
                os.replace(part, Path(f"{target}{suffix}"))
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    after = _core_counts(target)
    if before is None or after is None or before != after:
        _write_record(data_dir, candidate, version, removed=False)
        return AdoptionResult(
            True,
            f"copied {candidate.database}; the previous folder was kept because "
            "the row counts could not be confirmed",
            source=candidate.database,
            schema_version=version,
        )

    removed = _remove_previous(candidate)
    _write_record(data_dir, candidate, version, removed=removed)
    if removed:
        reason = f"adopted the database from {candidate.database} and removed it"
    else:
        reason = (
            f"adopted the database from {candidate.database}; the previous "
            "folder could not be removed"
        )
    return AdoptionResult(
        True,
        reason,
        source=candidate.database,
        schema_version=version,
        removed=removed,
    )


def _remove_previous(candidate: _Candidate) -> bool:
    """Delete the adopted folder, then its now-empty identifier directory."""
    if candidate.folder != candidate.owner:
        if candidate.folder.parent != candidate.owner:
            return False
    try:
        shutil.rmtree(candidate.folder)
    except OSError:
        return False
    if candidate.folder != candidate.owner:
        with contextlib.suppress(OSError):
            candidate.owner.rmdir()
    return True


def _inspect(database: Path) -> tuple[bool, int, str]:
    """Check a staged copy before it replaces anything.

    Returns ``(usable, schema_version, detail)`` where ``detail`` explains a
    refusal in words that can be logged.
    """
    try:
        conn = sqlite3.connect(f"file:{database}?mode=rw", uri=True)
    except sqlite3.Error as exc:
        return False, 0, f"could not be opened ({exc})"
    try:
        rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check")]
        if rows != ["ok"]:
            return False, 0, "failed SQLite's integrity check"
        # Fold a copied WAL back into the main file before it is moved.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        marker = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone()
        if marker is None:
            return False, 0, "is not a Job Signal database"
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        return True, int(row[0]) if row else 0, "ok"
    except sqlite3.Error as exc:
        return False, 0, f"failed SQLite's integrity check ({exc})"
    finally:
        conn.close()


def _core_counts(database: Path) -> dict[str, int] | None:
    """Row counts for the core tables this database actually has."""
    try:
        conn = sqlite3.connect(database)
    except sqlite3.Error:
        return None
    try:
        present = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        counts: dict[str, int] = {}
        for table in CORE_TABLES:
            if table not in present:
                continue
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = int(row[0]) if row else 0
        return counts or None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _write_record(
    data_dir: Path,
    candidate: _Candidate,
    version: int,
    *,
    removed: bool,
) -> None:
    """Leave a note in the data directory explaining where the rows came from."""
    payload = {
        "source": str(candidate.database),
        "identifier": candidate.identifier,
        "adopted_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": version,
        "previous_folder_removed": removed,
    }
    (data_dir / RECORD_NAME).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
