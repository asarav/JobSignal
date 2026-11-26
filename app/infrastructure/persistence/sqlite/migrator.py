from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
PRAGMAS = (
    "PRAGMA journal_mode = WAL;",
    "PRAGMA foreign_keys = ON;",
    "PRAGMA synchronous = NORMAL;",
    "PRAGMA busy_timeout = 5000;",
    "PRAGMA temp_store = MEMORY;",
    "PRAGMA cache_size = -20000;",
)


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


async def repair_job_titles(conn: aiosqlite.Connection) -> None:
    """Recompute the derived title columns with the current normalizer.

    SQLite has no Unicode case folding, so a migration cannot fix stored
    ``normalized_title``/``title_family`` values itself.
    """
    from app.domain.services.normalizer import normalize_title, title_family

    cur = await conn.execute("SELECT id, title, normalized_title, title_family FROM jobs")
    updates = []
    for job_id, title, stored_normalized, stored_family in await cur.fetchall():
        normalized = normalize_title(title or "")
        family = title_family(normalized)
        if (normalized, family) != (stored_normalized, stored_family):
            updates.append((normalized, family, job_id))
    if updates:
        await conn.executemany(
            "UPDATE jobs SET normalized_title = ?, title_family = ? WHERE id = ?",
            updates,
        )


#: One-off repairs SQLite cannot express, keyed by the migration version they
#: complete. Each runs once, right after that migration, and commits with it.
MIGRATION_REPAIRS = {11: repair_job_titles}


async def apply_pragmas(conn: aiosqlite.Connection) -> None:
    for pragma in PRAGMAS:
        await conn.execute(pragma)


async def backup_database(db_path: Path) -> Path | None:
    if not db_path.exists():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = db_path.with_suffix(db_path.suffix + f".{stamp}.bak")
    shutil.copy2(db_path, dest)
    return dest


#: Raised when SQLite reports a damaged file. The worker blocks startup
#: rather than writing into a database it cannot trust.
INTEGRITY_ERROR = (
    "database integrity check failed; restore a backup from <data-dir>/backups "
    "before starting Job Signal again"
)


async def check_integrity(conn: aiosqlite.Connection) -> None:
    """Run ``PRAGMA quick_check`` and refuse a damaged database (spec §17.4)."""
    cur = await conn.execute("PRAGMA quick_check")
    rows = [str(row[0]) for row in await cur.fetchall()]
    if rows != ["ok"]:
        detail = ", ".join(rows[:5]) or "no result"
        raise RuntimeError(f"{INTEGRITY_ERROR} ({detail})")


async def migrate(db_path: Path) -> int:
    try:
        return await _migrate(db_path)
    except aiosqlite.DatabaseError as exc:
        raise RuntimeError(INTEGRITY_ERROR) from exc


async def _migrate(db_path: Path) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    files = list_migration_files()
    async with aiosqlite.connect(db_path) as conn:
        await apply_pragmas(conn)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              applied_at TEXT NOT NULL,
              checksum TEXT NOT NULL
            ) STRICT
            """
        )
        await conn.commit()
        cur = await conn.execute("SELECT version, checksum FROM schema_migrations")
        applied = {int(row[0]): str(row[1]) for row in await cur.fetchall()}
        pending = []
        for path in files:
            version = int(path.name.split("_", 1)[0])
            sql = path.read_text(encoding="utf-8")
            checksum = _checksum(sql)
            if version in applied:
                if applied[version] != checksum:
                    raise RuntimeError(f"migration checksum mismatch: {path.name}")
                continue
            pending.append((version, path.name, sql, checksum))
        if pending:
            await backup_database(db_path)
        latest = max(applied) if applied else 0
        for version, name, sql, checksum in pending:
            await conn.executescript(sql)
            repair = MIGRATION_REPAIRS.get(version)
            if repair is not None:
                await repair(conn)
            await conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at, checksum) "
                "VALUES (?, ?, ?, ?)",
                (version, name, _iso_now(), checksum),
            )
            await conn.commit()
            latest = version
        await check_integrity(conn)
        return latest


def migration_checksum(path: Path) -> str:
    return _checksum(path.read_text(encoding="utf-8"))
