"""SQLite adapters for company merges and per-posting keywords (M9).

Both tables are append-only; the triggers in migrations 0015/0016 abort an
UPDATE or DELETE. Reads are batched: prevalence never issues one query per job.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import aiosqlite

from app.domain.models.company_merge import CompanyMergeRecord
from app.domain.models.job_keyword import (
    JobKeywordRecord,
    JobKeywordRevisionRecord,
)


def _iso(value: Any) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


def _merge_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": data["id"],
        "survivor_id": data["survivor_id"],
        "merged_id": data["merged_id"],
        "action": data["action"],
        "reason": data["reason"],
        "created_by": data["created_by"],
        "created_at": data["created_at"],
    }


class SqliteCompanyMergeRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(self, record: CompanyMergeRecord) -> None:
        await self._conn.execute(
            "INSERT INTO company_merges("
            "id, survivor_id, merged_id, action, reason, created_by, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.survivor_id,
                record.merged_id,
                record.action,
                record.reason,
                record.created_by,
                _iso(record.created_at),
            ),
        )

    async def latest_actions(
        self, *, limit: int = 5000
    ) -> Sequence[Mapping[str, object]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            """
            SELECT cm.* FROM company_merges AS cm
            JOIN (
              SELECT merged_id, MAX(rowid) AS rid
              FROM company_merges
              GROUP BY merged_id
            ) AS latest ON latest.rid = cm.rowid
            ORDER BY cm.rowid DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [_merge_row(row) for row in await cur.fetchall()]

    async def list_for_company(
        self, company_id: str, *, limit: int = 100
    ) -> Sequence[Mapping[str, object]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM company_merges "
            "WHERE merged_id = ? OR survivor_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (company_id, company_id, limit),
        )
        return [_merge_row(row) for row in await cur.fetchall()]

    async def list_all(self, *, limit: int = 200) -> Sequence[Mapping[str, object]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM company_merges ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        return [_merge_row(row) for row in await cur.fetchall()]


class SqliteJobKeywordRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add_revision(
        self,
        revision: JobKeywordRevisionRecord,
        keywords: Sequence[JobKeywordRecord],
    ) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO job_keyword_revisions("
            "id, job_id, revision_hash, taxonomy_version, keyword_count, extracted_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                revision.id,
                revision.job_id,
                revision.revision_hash,
                revision.taxonomy_version,
                revision.keyword_count,
                _iso(revision.extracted_at),
            ),
        )
        await self._conn.executemany(
            "INSERT OR IGNORE INTO job_keywords("
            "id, job_id, revision_hash, keyword_id, mentions, importance, "
            "taxonomy_version, extracted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    keyword.id,
                    keyword.job_id,
                    keyword.revision_hash,
                    keyword.keyword_id,
                    keyword.mentions,
                    keyword.importance,
                    keyword.taxonomy_version,
                    _iso(keyword.extracted_at),
                )
                for keyword in keywords
            ],
        )

    async def latest_revision(self, job_id: str) -> Mapping[str, object] | None:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM job_keyword_revisions WHERE job_id = ? "
            "ORDER BY extracted_at DESC, rowid DESC LIMIT 1",
            (job_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def latest_revisions_for_jobs(
        self, job_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, object]]:
        wanted = [str(value) for value in job_ids if value]
        if not wanted:
            return {}
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            f"""
            SELECT r.* FROM job_keyword_revisions AS r
            JOIN (
              SELECT job_id, MAX(rowid) AS rid
              FROM job_keyword_revisions
              WHERE job_id IN ({_placeholders(len(wanted))})
              GROUP BY job_id
            ) AS latest ON latest.rid = r.rowid
            """,
            tuple(wanted),
        )
        return {str(row["job_id"]): dict(row) for row in await cur.fetchall()}

    async def keywords_for_jobs(
        self, job_ids: Sequence[str]
    ) -> Mapping[str, Sequence[str]]:
        rows = await self.keywords_with_mentions(job_ids)
        return {job_id: sorted(values) for job_id, values in rows.items()}

    async def keywords_with_mentions(
        self, job_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, int]]:
        wanted = [str(value) for value in job_ids if value]
        if not wanted:
            return {}
        self._conn.row_factory = aiosqlite.Row
        out: dict[str, dict[str, int]] = {}
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            cur = await self._conn.execute(
                f"""
                SELECT k.job_id, k.keyword_id, MAX(k.mentions) AS mentions
                FROM job_keywords AS k
                JOIN (
                  SELECT job_id, MAX(rowid) AS rid
                  FROM job_keyword_revisions
                  WHERE job_id IN ({_placeholders(len(chunk))})
                  GROUP BY job_id
                ) AS latest ON latest.job_id = k.job_id
                WHERE k.job_id IN ({_placeholders(len(chunk))})
                  AND k.revision_hash = (
                    SELECT r.revision_hash FROM job_keyword_revisions AS r
                    WHERE r.rowid = latest.rid
                  )
                GROUP BY k.job_id, k.keyword_id
                """,
                (*chunk, *chunk),
            )
            for row in await cur.fetchall():
                out.setdefault(str(row["job_id"]), {})[str(row["keyword_id"])] = int(
                    row["mentions"]
                )
        return out

    async def keyword_rows_for_jobs(
        self, job_ids: Sequence[str]
    ) -> Sequence[Mapping[str, object]]:
        """Every stored keyword row for these jobs, in one batched read.

        Callers filter to the revision they care about; the repository never
        issues a query per job and never a query per keyword.
        """
        wanted = [str(value) for value in job_ids if value]
        if not wanted:
            return []
        self._conn.row_factory = aiosqlite.Row
        rows: list[dict[str, object]] = []
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            cur = await self._conn.execute(
                f"""
                SELECT job_id, revision_hash, keyword_id, mentions
                FROM job_keywords
                WHERE job_id IN ({_placeholders(len(chunk))})
                ORDER BY job_id, revision_hash
                """,
                chunk,
            )
            rows.extend(dict(row) for row in await cur.fetchall())
        return rows

    async def jobs_with_revisions(
        self, *, company_ids: Sequence[str] | None = None, limit: int = 5000
    ) -> Sequence[str]:
        self._conn.row_factory = aiosqlite.Row
        if company_ids:
            wanted = [str(value) for value in company_ids if value]
            if not wanted:
                return []
            cur = await self._conn.execute(
                f"""
                SELECT DISTINCT j.id FROM jobs AS j
                JOIN job_keyword_revisions AS r ON r.job_id = j.id
                WHERE j.company_id IN ({_placeholders(len(wanted))})
                  AND r.keyword_count > 0
                ORDER BY j.id LIMIT ?
                """,
                (*wanted, limit),
            )
        else:
            cur = await self._conn.execute(
                "SELECT DISTINCT j.id FROM jobs AS j "
                "JOIN job_keyword_revisions AS r ON r.job_id = j.id "
                "WHERE r.keyword_count > 0 "
                "ORDER BY j.id LIMIT ?",
                (limit,),
            )
        return [str(row["id"]) for row in await cur.fetchall()]

    async def jobs_missing_revisions(self, *, limit: int = 500) -> Sequence[str]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT j.id FROM jobs AS j "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM job_keyword_revisions AS r WHERE r.job_id = j.id"
            ") ORDER BY j.first_seen_at DESC LIMIT ?",
            (limit,),
        )
        return [str(row["id"]) for row in await cur.fetchall()]


def decode_evidence(value: Any) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    try:
        return dict(json.loads(str(value or "{}")))
    except ValueError:
        return {}


__all__ = ["SqliteCompanyMergeRepository", "SqliteJobKeywordRepository"]
