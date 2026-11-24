"""In-memory twins for company merges and per-posting keywords."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.domain.models.company_merge import CompanyMergeRecord
from app.domain.models.job_keyword import (
    JobKeywordRecord,
    JobKeywordRevisionRecord,
)


def _iso(value: Any) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class InMemoryCompanyMergeRepository:
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    async def add(self, record: CompanyMergeRecord) -> None:
        self._rows.append(
            {
                "id": record.id,
                "survivor_id": record.survivor_id,
                "merged_id": record.merged_id,
                "action": record.action,
                "reason": record.reason,
                "created_by": record.created_by,
                "created_at": _iso(record.created_at),
            }
        )

    async def latest_actions(
        self, *, limit: int = 5000
    ) -> Sequence[Mapping[str, object]]:
        latest: dict[str, dict[str, Any]] = {}
        for row in self._rows:
            latest[str(row["merged_id"])] = dict(row)
        ordered = sorted(
            latest.values(), key=lambda row: str(row["created_at"]), reverse=True
        )
        return ordered[:limit]

    async def list_for_company(
        self, company_id: str, *, limit: int = 100
    ) -> Sequence[Mapping[str, object]]:
        rows = [
            dict(row)
            for row in self._rows
            if row["merged_id"] == company_id or row["survivor_id"] == company_id
        ]
        rows.reverse()
        return rows[:limit]

    async def list_all(self, *, limit: int = 200) -> Sequence[Mapping[str, object]]:
        rows = [dict(row) for row in self._rows]
        rows.reverse()
        return rows[:limit]


class InMemoryJobKeywordRepository:
    def __init__(self, jobs: dict[str, dict[str, object]] | None = None) -> None:
        self._revisions: list[dict[str, Any]] = []
        self._keywords: list[dict[str, Any]] = []
        self._jobs = jobs if jobs is not None else {}

    async def add_revision(
        self,
        revision: JobKeywordRevisionRecord,
        keywords: Sequence[JobKeywordRecord],
    ) -> None:
        exists = any(
            row["job_id"] == revision.job_id
            and row["revision_hash"] == revision.revision_hash
            for row in self._revisions
        )
        if not exists:
            self._revisions.append(
                {
                    "id": revision.id,
                    "job_id": revision.job_id,
                    "revision_hash": revision.revision_hash,
                    "taxonomy_version": revision.taxonomy_version,
                    "keyword_count": revision.keyword_count,
                    "extracted_at": _iso(revision.extracted_at),
                }
            )
        present = {
            (row["job_id"], row["revision_hash"], row["keyword_id"])
            for row in self._keywords
        }
        for keyword in keywords:
            key = (keyword.job_id, keyword.revision_hash, keyword.keyword_id)
            if key in present:
                continue
            present.add(key)
            self._keywords.append(
                {
                    "id": keyword.id,
                    "job_id": keyword.job_id,
                    "revision_hash": keyword.revision_hash,
                    "keyword_id": keyword.keyword_id,
                    "mentions": keyword.mentions,
                    "taxonomy_version": keyword.taxonomy_version,
                    "extracted_at": _iso(keyword.extracted_at),
                }
            )

    def _latest_for(self, job_id: str) -> dict[str, Any] | None:
        rows = [
            (index, row)
            for index, row in enumerate(self._revisions)
            if row["job_id"] == job_id
        ]
        if not rows:
            return None
        rows.sort(
            key=lambda pair: (str(pair[1]["extracted_at"]), pair[0]), reverse=True
        )
        return dict(rows[0][1])

    async def latest_revision(self, job_id: str) -> Mapping[str, object] | None:
        return self._latest_for(job_id)

    async def latest_revisions_for_jobs(
        self, job_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, object]]:
        out: dict[str, Mapping[str, object]] = {}
        for job_id in job_ids:
            row = self._latest_for(str(job_id))
            if row is not None:
                out[str(job_id)] = row
        return out

    async def keywords_for_jobs(
        self, job_ids: Sequence[str]
    ) -> Mapping[str, Sequence[str]]:
        rows = await self.keywords_with_mentions(job_ids)
        return {job_id: sorted(values) for job_id, values in rows.items()}

    async def keywords_with_mentions(
        self, job_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for job_id in job_ids:
            latest = self._latest_for(str(job_id))
            if latest is None:
                continue
            bucket: dict[str, int] = {}
            for row in self._keywords:
                if (
                    row["job_id"] == latest["job_id"]
                    and row["revision_hash"] == latest["revision_hash"]
                ):
                    bucket[str(row["keyword_id"])] = max(
                        bucket.get(str(row["keyword_id"]), 0), int(row["mentions"])
                    )
            if bucket:
                out[str(job_id)] = bucket
        return out

    async def keyword_rows_for_jobs(
        self, job_ids: Sequence[str]
    ) -> Sequence[Mapping[str, object]]:
        wanted = {str(value) for value in job_ids if value}
        return [
            dict(row) for row in self._keywords if str(row["job_id"]) in wanted
        ]

    async def jobs_with_revisions(
        self, *, company_ids: Sequence[str] | None = None, limit: int = 5000
    ) -> Sequence[str]:
        wanted = {str(value) for value in company_ids or () if value}
        out: list[str] = []
        for row in self._revisions:
            if int(row.get("keyword_count") or 0) <= 0:
                # No extractable text is missing evidence, not zero demand.
                continue
            job_id = str(row["job_id"])
            if job_id in out:
                continue
            job = self._jobs.get(job_id)
            if wanted and (job is None or str(job.get("company_id")) not in wanted):
                continue
            out.append(job_id)
            if len(out) >= limit:
                break
        return out

    async def jobs_missing_revisions(self, *, limit: int = 500) -> Sequence[str]:
        seen = {str(row["job_id"]) for row in self._revisions}
        out = [str(job_id) for job_id in self._jobs if str(job_id) not in seen]
        return out[:limit]


__all__ = [
    "InMemoryCompanyMergeRepository",
    "InMemoryJobKeywordRepository",
]
