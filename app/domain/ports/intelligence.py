"""Repository ports for posting intelligence (spec §9.6).

The domain owns these Protocols so the evaluation pipeline depends on history,
never on SQLite. Every write here appends; "latest" is a read concern.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from app.domain.models.posting import (
    ClassificationChangeRecord,
    CompanySummaryRecord,
    EndpointObservationRecord,
    RiskAssessmentRecord,
    SignalObservationRecord,
)


class SignalObservationRepository(Protocol):
    async def upsert_registry(self, entries: Sequence[Mapping[str, object]]) -> None: ...

    async def list_registry(self) -> Sequence[Mapping[str, object]]: ...

    async def add(self, record: SignalObservationRecord) -> None: ...

    async def list_by_ids(
        self, ids: Sequence[str]
    ) -> Sequence[Mapping[str, object]]: ...


class RiskAssessmentRepository(Protocol):
    async def add(self, record: RiskAssessmentRecord) -> None: ...

    async def latest(self, job_id: str) -> Mapping[str, object] | None: ...

    async def latest_for_jobs(
        self, job_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, object]]: ...

    async def list_for_job(
        self, job_id: str, *, limit: int = 50
    ) -> Sequence[Mapping[str, object]]: ...

    async def find_by_input_hash(
        self, job_id: str, input_hash: str
    ) -> Mapping[str, object] | None: ...

    async def count_latest_in_classes(
        self,
        classes: Sequence[str],
        *,
        exclude_job_ids: Sequence[str] | None = None,
    ) -> int:
        """Count jobs whose latest assessment falls in ``classes``.

        This is a whole-corpus read for dashboard headline numbers: the
        per-job limit on ranking windows must not shrink a health count.
        """
        ...


class RiskModelVersionRepository(Protocol):
    async def ensure(
        self,
        *,
        model_id: str,
        model_version: str,
        config_json: str,
        config_hash: str,
        description: str = "",
    ) -> str: ...

    async def latest(self, model_id: str) -> Mapping[str, object] | None: ...

    async def list(self, model_id: str) -> Sequence[Mapping[str, object]]: ...


class CompanySummaryRepository(Protocol):
    async def add(self, record: CompanySummaryRecord) -> None: ...

    async def latest(self, company_id: str) -> Mapping[str, object] | None: ...

    async def latest_for_companies(
        self, company_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, object]]: ...

    async def for_day(
        self, company_id: str, day_prefix: str
    ) -> Mapping[str, object] | None: ...


class EndpointObservationRepository(Protocol):
    async def add(self, record: EndpointObservationRecord) -> None: ...

    async def latest(self, job_id: str) -> Mapping[str, object] | None: ...

    async def latest_for_jobs(
        self, job_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, object]]: ...

    async def list_for_job(
        self, job_id: str, *, limit: int = 50
    ) -> Sequence[Mapping[str, object]]: ...


class ClassificationChangeRepository(Protocol):
    async def add(self, record: ClassificationChangeRecord) -> None: ...

    async def list_for_job(
        self, job_id: str, *, limit: int = 50
    ) -> Sequence[Mapping[str, object]]: ...


class PostingIntelligenceRepositories(Protocol):
    """The bundle a unit of work exposes as ``uow.intelligence``."""

    signals: SignalObservationRepository
    assessments: RiskAssessmentRepository
    model_versions: RiskModelVersionRepository
    company_summaries: CompanySummaryRepository
    endpoints: EndpointObservationRepository
    classification_changes: ClassificationChangeRepository
