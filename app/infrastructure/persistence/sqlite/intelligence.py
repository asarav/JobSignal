"""SQLite repositories for posting-intelligence history (spec §9.6).

Reads return plain dicts with JSON columns decoded, matching the rest of the
SQLite adapters. Writes only ever INSERT: the tables have triggers that abort an
UPDATE or DELETE so history cannot be rewritten by accident.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import aiosqlite

from app.domain.models.posting import (
    ClassificationChangeRecord,
    CompanySummaryRecord,
    EndpointObservationRecord,
    RiskAssessmentRecord,
    SignalObservationRecord,
)


def _iso(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value is not None else None


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


class SqliteSignalObservationRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def upsert_registry(self, entries: Sequence[Mapping[str, object]]) -> None:
        await self._conn.executemany(
            """
            INSERT INTO posting_signals(
              signal_id, description, provider_version, default_weight, enabled
            ) VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(signal_id) DO UPDATE SET
              description = excluded.description,
              provider_version = excluded.provider_version,
              default_weight = excluded.default_weight
            """,
            [
                (
                    str(entry["signal_id"]),
                    str(entry.get("description", "")),
                    str(entry.get("provider_version", "")),
                    float(entry.get("default_weight", 0.0)),
                )
                for entry in entries
            ],
        )

    async def list_registry(self) -> Sequence[Mapping[str, object]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM posting_signals ORDER BY signal_id"
        )
        return [dict(row) for row in await cur.fetchall()]

    async def add(self, record: SignalObservationRecord) -> None:
        signal = record.signal
        await self._conn.execute(
            """
            INSERT INTO signal_observations(
              id, job_id, signal_id, provider_version, status, risk_contribution,
              confidence, strong, evidence_json, evidence_source, observed_at,
              evaluated_at, missing_reason, explanation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.job_id,
                signal.signal_id,
                signal.provider_version,
                signal.status.value,
                signal.risk_contribution,
                float(signal.confidence),
                1 if signal.strong else 0,
                _json(
                    [
                        {
                            "kind": item.kind,
                            "description": item.description,
                            "data": dict(item.data),
                            "observed_at": _iso(item.observed_at),
                            "source_ref": item.source_ref,
                        }
                        for item in signal.evidence
                    ]
                ),
                signal.evidence_source or None,
                _iso(signal.observed_at),
                _iso(record.evaluated_at),
                signal.missing_reason,
                signal.explanation,
            ),
        )

    async def list_by_ids(
        self, ids: Sequence[str]
    ) -> Sequence[Mapping[str, object]]:
        wanted = [str(value) for value in ids if value]
        if not wanted:
            return []
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            f"SELECT * FROM signal_observations WHERE id IN ({_placeholders(len(wanted))})",
            tuple(wanted),
        )
        return [_signal_row(dict(row)) for row in await cur.fetchall()]


def _signal_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "job_id": row["job_id"],
        "signal_id": row["signal_id"],
        "provider_version": row["provider_version"],
        "status": row["status"],
        "risk_contribution": row["risk_contribution"],
        "confidence": float(row["confidence"]),
        "strong": bool(row["strong"]),
        "evidence": json.loads(row["evidence_json"] or "[]"),
        "evidence_source": row["evidence_source"],
        "observed_at": row["observed_at"],
        "evaluated_at": row["evaluated_at"],
        "missing_reason": row["missing_reason"],
        "explanation": row["explanation"],
    }


class SqliteRiskAssessmentRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(self, record: RiskAssessmentRecord) -> None:
        assessment = record.assessment
        await self._conn.execute(
            """
            INSERT INTO risk_assessments(
              id, job_id, risk_model_version_id, risk, confidence, class,
              evidence_sufficiency, signal_observation_ids_json,
              top_contributors_json, evaluated_at, input_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.job_id,
                record.model_version_id,
                float(assessment.risk),
                float(assessment.confidence),
                assessment.risk_class.value,
                assessment.evidence_sufficiency,
                _json(list(record.signal_observation_ids)),
                _json(list(assessment.top_signals)),
                _iso(record.assessment.evaluated_at) or _iso(datetime.now()),
                record.input_hash,
            ),
        )

    async def latest(self, job_id: str) -> Mapping[str, object] | None:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM risk_assessments WHERE job_id = ? "
            "ORDER BY evaluated_at DESC, rowid DESC LIMIT 1",
            (job_id,),
        )
        row = await cur.fetchone()
        return _assessment_row(dict(row)) if row else None

    async def latest_for_jobs(
        self, job_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, object]]:
        wanted = [str(value) for value in job_ids if value]
        if not wanted:
            return {}
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            f"""
            SELECT ra.* FROM risk_assessments AS ra
            JOIN (
              SELECT job_id, MAX(evaluated_at) AS at
              FROM risk_assessments
              WHERE job_id IN ({_placeholders(len(wanted))})
              GROUP BY job_id
            ) AS latest
              ON latest.job_id = ra.job_id AND latest.at = ra.evaluated_at
            ORDER BY ra.rowid DESC
            """,
            tuple(wanted),
        )
        out: dict[str, Mapping[str, object]] = {}
        for row in await cur.fetchall():
            decoded = _assessment_row(dict(row))
            out.setdefault(str(decoded["job_id"]), decoded)
        return out

    async def list_for_job(
        self, job_id: str, *, limit: int = 50
    ) -> Sequence[Mapping[str, object]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM risk_assessments WHERE job_id = ? "
            "ORDER BY evaluated_at DESC, rowid DESC LIMIT ?",
            (job_id, limit),
        )
        return [_assessment_row(dict(row)) for row in await cur.fetchall()]

    async def find_by_input_hash(
        self, job_id: str, input_hash: str
    ) -> Mapping[str, object] | None:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM risk_assessments WHERE job_id = ? AND input_hash = ? "
            "ORDER BY evaluated_at DESC LIMIT 1",
            (job_id, input_hash),
        )
        row = await cur.fetchone()
        return _assessment_row(dict(row)) if row else None

    async def count_latest_in_classes(
        self,
        classes: Sequence[str],
        *,
        exclude_job_ids: Sequence[str] | None = None,
    ) -> int:
        wanted = [str(value) for value in classes if value]
        if not wanted:
            return 0
        excluded = [str(value) for value in (exclude_job_ids or ()) if value]
        exclusion = (
            f" AND ra.job_id NOT IN ({_placeholders(len(excluded))})"
            if excluded
            else ""
        )
        params: list[Any] = [*wanted, *excluded]
        cur = await self._conn.execute(
            f"""
            SELECT COUNT(*) FROM risk_assessments AS ra
            JOIN (
              SELECT job_id, MAX(rowid) AS rid
              FROM risk_assessments GROUP BY job_id
            ) AS latest ON latest.rid = ra.rowid
            JOIN jobs AS j ON j.id = ra.job_id
            WHERE ra.class IN ({_placeholders(len(wanted))}){exclusion}
            """,
            tuple(params),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0


def _assessment_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "job_id": row["job_id"],
        "risk_model_version_id": row["risk_model_version_id"],
        "risk": float(row["risk"]),
        "confidence": float(row["confidence"]),
        "class": row["class"],
        "evidence_sufficiency": row["evidence_sufficiency"],
        "signal_observation_ids": json.loads(
            row["signal_observation_ids_json"] or "[]"
        ),
        "top_signals": json.loads(row["top_contributors_json"] or "[]"),
        "evaluated_at": row["evaluated_at"],
        "input_hash": row["input_hash"],
    }


class SqliteRiskModelVersionRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def ensure(
        self,
        *,
        model_id: str,
        model_version: str,
        config_json: str,
        config_hash: str,
        description: str = "",
    ) -> str:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT id FROM risk_model_versions "
            "WHERE model_id = ? AND model_version = ? AND config_hash = ?",
            (model_id, model_version, config_hash),
        )
        row = await cur.fetchone()
        if row is not None:
            return str(row["id"])
        version_id = f"rmv_{uuid.uuid4().hex[:16]}"
        await self._conn.execute(
            "INSERT OR IGNORE INTO risk_model_versions("
            "id, model_id, model_version, config_json, config_hash, description, "
            "created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, 'system')",
            (
                version_id,
                model_id,
                model_version,
                config_json,
                config_hash,
                description or None,
                datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )
        cur = await self._conn.execute(
            "SELECT id FROM risk_model_versions "
            "WHERE model_id = ? AND model_version = ? AND config_hash = ?",
            (model_id, model_version, config_hash),
        )
        row = await cur.fetchone()
        return str(row["id"])

    async def latest(self, model_id: str) -> Mapping[str, object] | None:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM risk_model_versions WHERE model_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (model_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list(self, model_id: str) -> Sequence[Mapping[str, object]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM risk_model_versions WHERE model_id = ? "
            "ORDER BY created_at DESC, rowid DESC",
            (model_id,),
        )
        return [dict(row) for row in await cur.fetchall()]


class SqliteCompanySummaryRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(self, record: CompanySummaryRecord) -> None:
        await self._conn.execute(
            "INSERT INTO company_hiring_summaries("
            "id, company_id, computed_at, sample_tier, metrics_json, labels_json, "
            "evidence_json, methodology_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.company_id,
                _iso(record.computed_at),
                record.sample_tier,
                _json(dict(record.metrics)),
                _json(list(record.labels)),
                _json(list(record.evidence)),
                record.methodology_version,
            ),
        )

    async def latest(self, company_id: str) -> Mapping[str, object] | None:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM company_hiring_summaries WHERE company_id = ? "
            "ORDER BY computed_at DESC, rowid DESC LIMIT 1",
            (company_id,),
        )
        row = await cur.fetchone()
        return _summary_row(dict(row)) if row else None

    async def latest_for_companies(
        self, company_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, object]]:
        wanted = [str(value) for value in company_ids if value]
        if not wanted:
            return {}
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            f"""
            SELECT chs.* FROM company_hiring_summaries AS chs
            JOIN (
              SELECT company_id, MAX(computed_at) AS at
              FROM company_hiring_summaries
              WHERE company_id IN ({_placeholders(len(wanted))})
              GROUP BY company_id
            ) AS latest
              ON latest.company_id = chs.company_id AND latest.at = chs.computed_at
            ORDER BY chs.rowid DESC
            """,
            tuple(wanted),
        )
        out: dict[str, Mapping[str, object]] = {}
        for row in await cur.fetchall():
            decoded = _summary_row(dict(row))
            out.setdefault(str(decoded["company_id"]), decoded)
        return out

    async def for_day(
        self, company_id: str, day_prefix: str
    ) -> Mapping[str, object] | None:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM company_hiring_summaries "
            "WHERE company_id = ? AND computed_at LIKE ? "
            "ORDER BY computed_at DESC LIMIT 1",
            (company_id, f"{day_prefix}%"),
        )
        row = await cur.fetchone()
        return _summary_row(dict(row)) if row else None


def _summary_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "company_id": row["company_id"],
        "computed_at": row["computed_at"],
        "sample_tier": row["sample_tier"],
        "metrics": json.loads(row["metrics_json"] or "{}"),
        "labels": json.loads(row["labels_json"] or "[]"),
        "evidence": json.loads(row["evidence_json"] or "[]"),
        "methodology_version": row["methodology_version"],
    }


class SqliteEndpointObservationRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(self, record: EndpointObservationRecord) -> None:
        await self._conn.execute(
            "INSERT INTO application_endpoint_observations("
            "id, job_id, alias_id, observed_at, state, http_status, final_url, "
            "details_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.job_id,
                None,
                _iso(record.observed_at),
                record.state,
                record.http_status,
                record.final_url,
                _json(dict(record.details)),
            ),
        )

    async def latest(self, job_id: str) -> Mapping[str, object] | None:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM application_endpoint_observations WHERE job_id = ? "
            "ORDER BY observed_at DESC, rowid DESC LIMIT 1",
            (job_id,),
        )
        row = await cur.fetchone()
        return _endpoint_row(dict(row)) if row else None

    async def latest_for_jobs(
        self, job_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, object]]:
        wanted = [str(value) for value in job_ids if value]
        if not wanted:
            return {}
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            f"""
            SELECT eo.* FROM application_endpoint_observations AS eo
            JOIN (
              SELECT job_id, MAX(observed_at) AS at
              FROM application_endpoint_observations
              WHERE job_id IN ({_placeholders(len(wanted))})
              GROUP BY job_id
            ) AS latest
              ON latest.job_id = eo.job_id AND latest.at = eo.observed_at
            ORDER BY eo.rowid DESC
            """,
            tuple(wanted),
        )
        out: dict[str, Mapping[str, object]] = {}
        for row in await cur.fetchall():
            decoded = _endpoint_row(dict(row))
            out.setdefault(str(decoded["job_id"]), decoded)
        return out

    async def list_for_job(
        self, job_id: str, *, limit: int = 50
    ) -> Sequence[Mapping[str, object]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM application_endpoint_observations WHERE job_id = ? "
            "ORDER BY observed_at DESC, rowid DESC LIMIT ?",
            (job_id, limit),
        )
        return [_endpoint_row(dict(row)) for row in await cur.fetchall()]


def _endpoint_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "job_id": row["job_id"],
        "observed_at": row["observed_at"],
        "state": row["state"],
        "http_status": row["http_status"],
        "final_url": row["final_url"],
        "details": json.loads(row["details_json"] or "{}"),
    }


class SqliteClassificationChangeRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(self, record: ClassificationChangeRecord) -> None:
        await self._conn.execute(
            "INSERT INTO classification_changes("
            "id, job_id, from_class, to_class, from_assessment_id, "
            "to_assessment_id, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.job_id,
                record.from_class,
                record.to_class,
                record.from_assessment_id,
                record.to_assessment_id,
                _iso(record.changed_at),
            ),
        )

    async def list_for_job(
        self, job_id: str, *, limit: int = 50
    ) -> Sequence[Mapping[str, object]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM classification_changes WHERE job_id = ? "
            "ORDER BY changed_at DESC, rowid DESC LIMIT ?",
            (job_id, limit),
        )
        return [dict(row) for row in await cur.fetchall()]


class SqliteIntelligenceRepositories:
    """Bundle exposed as ``uow.intelligence``."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.signals = SqliteSignalObservationRepository(conn)
        self.assessments = SqliteRiskAssessmentRepository(conn)
        self.model_versions = SqliteRiskModelVersionRepository(conn)
        self.company_summaries = SqliteCompanySummaryRepository(conn)
        self.endpoints = SqliteEndpointObservationRepository(conn)
        self.classification_changes = SqliteClassificationChangeRepository(conn)


__all__ = [
    "SqliteClassificationChangeRepository",
    "SqliteCompanySummaryRepository",
    "SqliteEndpointObservationRepository",
    "SqliteIntelligenceRepositories",
    "SqliteRiskAssessmentRepository",
    "SqliteRiskModelVersionRepository",
    "SqliteSignalObservationRepository",
]
