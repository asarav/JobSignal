"""In-memory twins of the posting-intelligence repositories.

They must behave like the SQLite adapters (contract-tested both ways): appends
only, "latest" resolved by timestamp, JSON fields already decoded.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from app.domain.models.posting import (
    ClassificationChangeRecord,
    CompanySummaryRecord,
    EndpointObservationRecord,
    RiskAssessmentRecord,
    SignalObservationRecord,
)


def _iso(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value is not None else None


class InMemoryIntelligenceStores:
    """Shared storage so history survives across unit-of-work instances."""

    def __init__(self) -> None:
        self.registry: dict[str, dict[str, Any]] = {}
        self.signals: list[dict[str, Any]] = []
        self.assessments: list[dict[str, Any]] = []
        self.model_versions: list[dict[str, Any]] = []
        self.summaries: list[dict[str, Any]] = []
        self.endpoints: list[dict[str, Any]] = []
        self.changes: list[dict[str, Any]] = []


class InMemorySignalObservationRepository:
    def __init__(self, stores: InMemoryIntelligenceStores) -> None:
        self._stores = stores

    async def upsert_registry(self, entries: Sequence[Mapping[str, object]]) -> None:
        for entry in entries:
            signal_id = str(entry["signal_id"])
            self._stores.registry[signal_id] = {
                "signal_id": signal_id,
                "description": str(entry.get("description", "")),
                "provider_version": str(entry.get("provider_version", "")),
                "default_weight": float(entry.get("default_weight", 0.0)),
                "enabled": 1,
            }

    async def list_registry(self) -> Sequence[Mapping[str, object]]:
        return [
            dict(self._stores.registry[key])
            for key in sorted(self._stores.registry)
        ]

    async def add(self, record: SignalObservationRecord) -> None:
        signal = record.signal
        self._stores.signals.append(
            {
                "id": record.id,
                "job_id": record.job_id,
                "signal_id": signal.signal_id,
                "provider_version": signal.provider_version,
                "status": signal.status.value,
                "risk_contribution": signal.risk_contribution,
                "confidence": float(signal.confidence),
                "strong": bool(signal.strong),
                "evidence": [
                    {
                        "kind": item.kind,
                        "description": item.description,
                        "data": dict(item.data),
                        "observed_at": _iso(item.observed_at),
                        "source_ref": item.source_ref,
                    }
                    for item in signal.evidence
                ],
                "evidence_source": signal.evidence_source or None,
                "observed_at": _iso(signal.observed_at),
                "evaluated_at": _iso(record.evaluated_at),
                "missing_reason": signal.missing_reason,
                "explanation": signal.explanation,
            }
        )

    async def list_by_ids(
        self, ids: Sequence[str]
    ) -> Sequence[Mapping[str, object]]:
        wanted = {str(value) for value in ids if value}
        return [
            dict(row) for row in self._stores.signals if str(row["id"]) in wanted
        ]


class InMemoryRiskAssessmentRepository:
    def __init__(self, stores: InMemoryIntelligenceStores) -> None:
        self._stores = stores

    async def add(self, record: RiskAssessmentRecord) -> None:
        assessment = record.assessment
        self._stores.assessments.append(
            {
                "id": record.id,
                "job_id": record.job_id,
                "risk_model_version_id": record.model_version_id,
                "risk": float(assessment.risk),
                "confidence": float(assessment.confidence),
                "class": assessment.risk_class.value,
                "evidence_sufficiency": assessment.evidence_sufficiency,
                "signal_observation_ids": list(record.signal_observation_ids),
                "top_signals": list(assessment.top_signals),
                "evaluated_at": _iso(assessment.evaluated_at),
                "input_hash": record.input_hash,
            }
        )

    def _sorted(self, job_id: str) -> list[dict[str, Any]]:
        # Insertion order is the tiebreak: two assessments written in the same
        # second must resolve to the one written last, exactly like rowid in
        # SQLite.
        rows = [
            (index, row)
            for index, row in enumerate(self._stores.assessments)
            if row["job_id"] == job_id
        ]
        rows.sort(key=lambda pair: (str(pair[1]["evaluated_at"]), pair[0]), reverse=True)
        return [row for _, row in rows]

    async def latest(self, job_id: str) -> Mapping[str, object] | None:
        rows = self._sorted(job_id)
        return dict(rows[0]) if rows else None

    async def latest_for_jobs(
        self, job_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, object]]:
        out: dict[str, Mapping[str, object]] = {}
        for job_id in job_ids:
            latest = await self.latest(str(job_id))
            if latest is not None:
                out[str(job_id)] = latest
        return out

    async def list_for_job(
        self, job_id: str, *, limit: int = 50
    ) -> Sequence[Mapping[str, object]]:
        return [dict(row) for row in self._sorted(job_id)[:limit]]

    async def find_by_input_hash(
        self, job_id: str, input_hash: str
    ) -> Mapping[str, object] | None:
        for row in self._sorted(job_id):
            if row["input_hash"] == input_hash:
                return dict(row)
        return None

    async def count_latest_in_classes(
        self,
        classes: Sequence[str],
        *,
        exclude_job_ids: Sequence[str] | None = None,
    ) -> int:
        wanted = {str(value) for value in classes if value}
        excluded = {str(value) for value in (exclude_job_ids or ()) if value}
        job_ids = {str(row["job_id"]) for row in self._stores.assessments}
        total = 0
        for job_id in job_ids:
            if not job_id or job_id in excluded:
                continue
            latest = self._sorted(job_id)
            if latest and str(latest[0]["class"]) in wanted:
                total += 1
        return total


class InMemoryRiskModelVersionRepository:
    def __init__(self, stores: InMemoryIntelligenceStores) -> None:
        self._stores = stores

    async def ensure(
        self,
        *,
        model_id: str,
        model_version: str,
        config_json: str,
        config_hash: str,
        description: str = "",
    ) -> str:
        for row in self._stores.model_versions:
            if (
                row["model_id"] == model_id
                and row["model_version"] == model_version
                and row["config_hash"] == config_hash
            ):
                return str(row["id"])
        version_id = f"rmv_{uuid.uuid4().hex[:16]}"
        self._stores.model_versions.append(
            {
                "id": version_id,
                "model_id": model_id,
                "model_version": model_version,
                "config_json": config_json,
                "config_hash": config_hash,
                "description": description,
                "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "created_by": "system",
            }
        )
        return version_id

    async def latest(self, model_id: str) -> Mapping[str, object] | None:
        rows = [
            (index, row)
            for index, row in enumerate(self._stores.model_versions)
            if row["model_id"] == model_id
        ]
        if not rows:
            return None
        index, row = max(rows, key=lambda pair: (str(pair[1]["created_at"]), pair[0]))
        return dict(row)

    async def list(self, model_id: str) -> Sequence[Mapping[str, object]]:
        rows = [
            dict(row)
            for row in self._stores.model_versions
            if row["model_id"] == model_id
        ]
        return sorted(rows, key=lambda row: str(row["created_at"]), reverse=True)


class InMemoryCompanySummaryRepository:
    def __init__(self, stores: InMemoryIntelligenceStores) -> None:
        self._stores = stores

    async def add(self, record: CompanySummaryRecord) -> None:
        self._stores.summaries.append(
            {
                "id": record.id,
                "company_id": record.company_id,
                "computed_at": _iso(record.computed_at),
                "sample_tier": record.sample_tier,
                "metrics": json.loads(json.dumps(dict(record.metrics), default=str)),
                "labels": [dict(item) for item in record.labels],
                "evidence": [dict(item) for item in record.evidence],
                "methodology_version": record.methodology_version,
            }
        )

    def _ordered(self, company_id: str) -> list[dict[str, Any]]:
        rows = [
            (index, row)
            for index, row in enumerate(self._stores.summaries)
            if row["company_id"] == company_id
        ]
        rows.sort(key=lambda pair: (str(pair[1]["computed_at"]), pair[0]), reverse=True)
        return [row for _, row in rows]

    async def latest(self, company_id: str) -> Mapping[str, object] | None:
        rows = self._ordered(company_id)
        return dict(rows[0]) if rows else None

    async def latest_for_companies(
        self, company_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, object]]:
        out: dict[str, Mapping[str, object]] = {}
        for company_id in company_ids:
            latest = await self.latest(str(company_id))
            if latest is not None:
                out[str(company_id)] = latest
        return out

    async def for_day(
        self, company_id: str, day_prefix: str
    ) -> Mapping[str, object] | None:
        for row in self._ordered(company_id):
            if str(row["computed_at"]).startswith(day_prefix):
                return dict(row)
        return None

    async def daily_history(
        self, company_ids: Sequence[str], *, since: str, limit: int = 500
    ) -> Sequence[Mapping[str, object]]:
        wanted = {str(value) for value in company_ids if value}
        rows = [
            (index, row)
            for index, row in enumerate(self._stores.summaries)
            if str(row["company_id"]) in wanted
            and str(row["computed_at"]) >= since
        ]
        rows.sort(key=lambda pair: (str(pair[1]["computed_at"]), pair[0]))
        return [dict(row) for _, row in rows[: max(1, int(limit))]]


class InMemoryEndpointObservationRepository:
    def __init__(self, stores: InMemoryIntelligenceStores) -> None:
        self._stores = stores

    async def add(self, record: EndpointObservationRecord) -> None:
        self._stores.endpoints.append(
            {
                "id": record.id,
                "job_id": record.job_id,
                "observed_at": _iso(record.observed_at),
                "state": record.state,
                "http_status": record.http_status,
                "final_url": record.final_url,
                "details": dict(record.details),
            }
        )

    def _ordered(self, job_id: str) -> list[dict[str, Any]]:
        rows = [
            (index, row)
            for index, row in enumerate(self._stores.endpoints)
            if row["job_id"] == job_id
        ]
        rows.sort(key=lambda pair: (str(pair[1]["observed_at"]), pair[0]), reverse=True)
        return [row for _, row in rows]

    async def latest(self, job_id: str) -> Mapping[str, object] | None:
        rows = self._ordered(job_id)
        return dict(rows[0]) if rows else None

    async def latest_for_jobs(
        self, job_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, object]]:
        out: dict[str, Mapping[str, object]] = {}
        for job_id in job_ids:
            latest = await self.latest(str(job_id))
            if latest is not None:
                out[str(job_id)] = latest
        return out

    async def list_for_job(
        self, job_id: str, *, limit: int = 50
    ) -> Sequence[Mapping[str, object]]:
        return [dict(row) for row in self._ordered(job_id)[:limit]]


class InMemoryClassificationChangeRepository:
    def __init__(self, stores: InMemoryIntelligenceStores) -> None:
        self._stores = stores

    async def add(self, record: ClassificationChangeRecord) -> None:
        self._stores.changes.append(
            {
                "id": record.id,
                "job_id": record.job_id,
                "from_class": record.from_class,
                "to_class": record.to_class,
                "from_assessment_id": record.from_assessment_id,
                "to_assessment_id": record.to_assessment_id,
                "changed_at": _iso(record.changed_at),
            }
        )

    async def list_for_job(
        self, job_id: str, *, limit: int = 50
    ) -> Sequence[Mapping[str, object]]:
        rows = [
            (index, row)
            for index, row in enumerate(self._stores.changes)
            if row["job_id"] == job_id
        ]
        rows.sort(key=lambda pair: (str(pair[1]["changed_at"]), pair[0]), reverse=True)
        return [dict(row) for _, row in rows[:limit]]


class InMemoryIntelligenceRepositories:
    """Bundle exposed as ``uow.intelligence``."""

    def __init__(self, stores: InMemoryIntelligenceStores | None = None) -> None:
        self.stores = stores or InMemoryIntelligenceStores()
        self.signals = InMemorySignalObservationRepository(self.stores)
        self.assessments = InMemoryRiskAssessmentRepository(self.stores)
        self.model_versions = InMemoryRiskModelVersionRepository(self.stores)
        self.company_summaries = InMemoryCompanySummaryRepository(self.stores)
        self.endpoints = InMemoryEndpointObservationRepository(self.stores)
        self.classification_changes = InMemoryClassificationChangeRepository(
            self.stores
        )


__all__ = [
    "InMemoryIntelligenceRepositories",
    "InMemoryIntelligenceStores",
]
