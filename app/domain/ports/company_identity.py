"""Ports for company identity (merge audit) and per-posting keywords."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from app.domain.models.company_merge import CompanyMergeRecord
from app.domain.models.job_keyword import (
    JobKeywordRecord,
    JobKeywordRevisionRecord,
)


class CompanyMergeRepository(Protocol):
    async def add(self, record: CompanyMergeRecord) -> None: ...

    async def latest_actions(
        self, *, limit: int = 5000
    ) -> Sequence[Mapping[str, object]]:
        """Newest decision per merged company (all rows it needs to resolve)."""
        ...

    async def list_for_company(
        self, company_id: str, *, limit: int = 100
    ) -> Sequence[Mapping[str, object]]: ...

    async def list_all(self, *, limit: int = 200) -> Sequence[Mapping[str, object]]: ...


class JobKeywordRepository(Protocol):
    async def add_revision(
        self,
        revision: JobKeywordRevisionRecord,
        keywords: Sequence[JobKeywordRecord],
    ) -> None: ...

    async def latest_revision(self, job_id: str) -> Mapping[str, object] | None: ...

    async def latest_revisions_for_jobs(
        self, job_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, object]]: ...

    async def keywords_for_jobs(
        self, job_ids: Sequence[str]
    ) -> Mapping[str, Sequence[str]]: ...

    async def keywords_with_mentions(
        self, job_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, int]]: ...

    async def keyword_rows_for_jobs(
        self, job_ids: Sequence[str]
    ) -> Sequence[Mapping[str, object]]: ...

    async def jobs_with_revisions(
        self, *, company_ids: Sequence[str] | None = None, limit: int = 5000
    ) -> Sequence[str]: ...

    async def jobs_missing_revisions(self, *, limit: int = 500) -> Sequence[str]: ...
