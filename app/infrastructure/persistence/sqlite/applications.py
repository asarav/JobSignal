"""Application history, suppression and job-user-state repositories (M7).

The event log is append-only in the schema (triggers abort UPDATE/DELETE), so
these repositories only ever insert events. Suppression rows are the one place
where a stored decision changes: a policy rebuild updates an ``active`` row,
and expiry/restore move the state — the evidence of what was decided stays.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from app.domain.models.application import (
    Application,
    ApplicationEvent,
    ApplicationOutcome,
    AppliedJob,
    SuppressionRecord,
)


def _iso(dt: datetime) -> str:
    """UTC timestamp in the format the rest of the schema stores."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _json_map(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class SqliteApplicationRepository:
    def __init__(self, conn: aiosqlite.Connection, clock_now: Any) -> None:
        self._conn = conn
        self._now = clock_now
        self._conn.row_factory = aiosqlite.Row

    def _row(self, row: aiosqlite.Row) -> Application:
        return Application(
            id=row["id"],
            company_id=row["company_id"],
            job_id=row["job_id"],
            job_title=row["job_title"],
            job_title_family=row["job_title_family"],
            job_canonical_url=row["job_canonical_url"],
            job_description_text=row["job_description_text"],
            job_department=row["job_department"],
            job_locations=tuple(_json_list(row["job_locations_json"])),
            job_workplace_type=row["job_workplace_type"],
            job_snapshot_id=row["job_snapshot_id"],
            status=row["status"],
            applied_at=_parse(row["applied_at"]),
            resume_version_id=row["resume_version_id"],
            notes=row["notes"],
            created_at=_parse(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse(row["updated_at"]),  # type: ignore[arg-type]
        )

    async def add(self, application: Application) -> None:
        await self._conn.execute(
            """
            INSERT INTO applications(
              id, company_id, job_id, job_title, job_title_family,
              job_canonical_url, job_description_text, job_department,
              job_locations_json, job_workplace_type, job_snapshot_id,
              applied_at, status, resume_version_id, notes, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                application.id,
                application.company_id,
                application.job_id,
                application.job_title,
                application.job_title_family,
                application.job_canonical_url,
                application.job_description_text,
                application.job_department,
                json.dumps([dict(loc) for loc in application.job_locations]),
                application.job_workplace_type,
                application.job_snapshot_id,
                _iso(application.applied_at) if application.applied_at else None,
                application.status,
                application.resume_version_id,
                application.notes,
                _iso(application.created_at),
                _iso(application.updated_at),
            ),
        )

    async def get(self, application_id: str) -> Application | None:
        cur = await self._conn.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        )
        row = await cur.fetchone()
        return self._row(row) if row is not None else None

    async def list(self, *, limit: int = 50, offset: int = 0) -> Sequence[Application]:
        cur = await self._conn.execute(
            "SELECT * FROM applications ORDER BY COALESCE(applied_at, created_at) DESC, id "
            "LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [self._row(row) for row in await cur.fetchall()]

    async def save(
        self,
        application_id: str,
        *,
        status: str,
        applied_at: datetime | None,
        notes: str,
        resume_version_id: str | None,
        updated_at: datetime,
    ) -> Application | None:
        await self._conn.execute(
            """
            UPDATE applications
               SET status = ?, applied_at = ?, notes = ?,
                   resume_version_id = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                status,
                _iso(applied_at) if applied_at else None,
                notes,
                resume_version_id,
                _iso(updated_at),
                application_id,
            ),
        )
        return await self.get(application_id)

    async def find_for_job(self, job_id: str) -> Sequence[Application]:
        cur = await self._conn.execute(
            "SELECT * FROM applications WHERE job_id = ? ORDER BY created_at, id",
            (job_id,),
        )
        return [self._row(row) for row in await cur.fetchall()]

    async def list_applied(self, *, company_id: str | None = None) -> Sequence[AppliedJob]:
        """Every application that reached ``applied``, ready for suppression."""
        clauses = ["applied_at IS NOT NULL"]
        params: list[Any] = []
        if company_id:
            clauses.append("company_id = ?")
            params.append(company_id)
        cur = await self._conn.execute(
            "SELECT * FROM applications WHERE " + " AND ".join(clauses),
            tuple(params),
        )
        rows = await cur.fetchall()
        if not rows:
            return []
        relations = await self._related(job_ids=[str(row["job_id"]) for row in rows])
        out: list[AppliedJob] = []
        for row in rows:
            job_id = str(row["job_id"])
            out.append(
                AppliedJob(
                    application_id=str(row["id"]),
                    job_id=job_id,
                    company_id=str(row["company_id"]),
                    title=str(row["job_title"]),
                    title_family=row["job_title_family"],
                    description_text=str(row["job_description_text"] or ""),
                    canonical_url=str(row["job_canonical_url"] or ""),
                    applied_at=_parse(row["applied_at"]),  # type: ignore[arg-type]
                    department=row["job_department"],
                    locations=tuple(_json_list(row["job_locations_json"])),
                    workplace_type=str(row["job_workplace_type"] or "unknown"),
                    related_job_ids=tuple(relations.get(job_id, ())),
                )
            )
        return out

    async def _related(self, *, job_ids: Sequence[str]) -> dict[str, list[str]]:
        wanted = [job_id for job_id in dict.fromkeys(job_ids) if job_id]
        out: dict[str, list[str]] = {}
        for start in range(0, len(wanted), 400):
            chunk = wanted[start : start + 400]
            placeholders = ",".join("?" for _ in chunk)
            cur = await self._conn.execute(
                "SELECT job_id, related_job_id FROM job_relations "
                f"WHERE relation != 'user_unmerged' AND "
                f"(job_id IN ({placeholders}) OR related_job_id IN ({placeholders}))",
                tuple(chunk) + tuple(chunk),
            )
            for row in await cur.fetchall():
                left, right = str(row["job_id"]), str(row["related_job_id"])
                if left in chunk:
                    out.setdefault(left, []).append(right)
                if right in chunk:
                    out.setdefault(right, []).append(left)
        return out

    async def add_event(self, event: ApplicationEvent) -> None:
        await self._conn.execute(
            """
            INSERT INTO application_events(
              id, application_id, kind, from_status, to_status, at, note, data_json
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                event.id,
                event.application_id,
                event.kind,
                event.from_status,
                event.to_status,
                _iso(event.at),
                event.note,
                json.dumps(dict(event.data), default=str),
            ),
        )

    async def list_events(self, application_id: str) -> Sequence[ApplicationEvent]:
        cur = await self._conn.execute(
            "SELECT * FROM application_events WHERE application_id = ? ORDER BY at, id",
            (application_id,),
        )
        return [
            ApplicationEvent(
                id=row["id"],
                application_id=row["application_id"],
                kind=row["kind"],
                at=_parse(row["at"]),  # type: ignore[arg-type]
                from_status=row["from_status"],
                to_status=row["to_status"],
                note=row["note"],
                data=_json_map(row["data_json"]),
            )
            for row in await cur.fetchall()
        ]

    async def add_outcome(self, outcome: ApplicationOutcome) -> None:
        await self._conn.execute(
            """
            INSERT INTO application_outcomes(
              id, application_id, kind, occurred_at, note, proposed, confirmed, created_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                outcome.id,
                outcome.application_id,
                outcome.kind,
                _iso(outcome.occurred_at),
                outcome.note,
                1 if outcome.proposed else 0,
                1 if outcome.confirmed else 0,
                _iso(outcome.created_at or outcome.occurred_at),
            ),
        )

    async def list_outcomes(self, application_id: str) -> Sequence[ApplicationOutcome]:
        cur = await self._conn.execute(
            "SELECT * FROM application_outcomes WHERE application_id = ? "
            "ORDER BY occurred_at, id",
            (application_id,),
        )
        return [
            ApplicationOutcome(
                id=row["id"],
                application_id=row["application_id"],
                kind=row["kind"],
                occurred_at=_parse(row["occurred_at"]),  # type: ignore[arg-type]
                note=row["note"],
                proposed=bool(row["proposed"]),
                confirmed=bool(row["confirmed"]),
                created_at=_parse(row["created_at"]),
            )
            for row in await cur.fetchall()
        ]


class SqliteSuppressionRepository:
    def __init__(self, conn: aiosqlite.Connection, clock_now: Any) -> None:
        self._conn = conn
        self._now = clock_now
        self._conn.row_factory = aiosqlite.Row

    def _row(self, row: aiosqlite.Row) -> SuppressionRecord:
        return SuppressionRecord(
            id=row["id"],
            job_id=row["job_id"],
            application_id=row["application_id"],
            state=row["state"],
            reason=row["reason"],
            explanation=row["explanation"],
            similarity=float(row["similarity"] or 0.0),
            evidence=_json_map(row["evidence_json"]),
            policy=_json_map(row["policy_json"]),
            expires_at=_parse(row["expires_at"]),
            created_at=_parse(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse(row["updated_at"]),  # type: ignore[arg-type]
        )

    async def save(self, record: SuppressionRecord) -> None:
        """Insert a decision, or refresh an un-restored one.

        A restored pair stays restored: automation must never silently undo the
        user's restore (spec §8.2 rule 8).
        """
        await self._conn.execute(
            """
            INSERT INTO job_suppressions(
              id, job_id, application_id, state, reason, explanation, similarity,
              evidence_json, policy_json, expires_at, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(job_id, application_id) DO UPDATE SET
              state = excluded.state,
              reason = excluded.reason,
              explanation = excluded.explanation,
              similarity = excluded.similarity,
              evidence_json = excluded.evidence_json,
              policy_json = excluded.policy_json,
              expires_at = excluded.expires_at,
              updated_at = excluded.updated_at
            WHERE job_suppressions.state != 'restored'
            """,
            (
                record.id,
                record.job_id,
                record.application_id,
                record.state,
                record.reason,
                record.explanation,
                float(record.similarity),
                json.dumps(dict(record.evidence), default=str),
                json.dumps(dict(record.policy), default=str),
                _iso(record.expires_at) if record.expires_at else None,
                _iso(record.created_at),
                _iso(record.updated_at),
            ),
        )

    async def get(self, job_id: str, application_id: str) -> SuppressionRecord | None:
        cur = await self._conn.execute(
            "SELECT * FROM job_suppressions WHERE job_id = ? AND application_id = ?",
            (job_id, application_id),
        )
        row = await cur.fetchone()
        return self._row(row) if row is not None else None

    async def list(
        self, *, state: str | None = None, limit: int = 200
    ) -> Sequence[SuppressionRecord]:
        if state:
            cur = await self._conn.execute(
                "SELECT * FROM job_suppressions WHERE state = ? "
                "ORDER BY created_at DESC, id LIMIT ?",
                (state, limit),
            )
        else:
            cur = await self._conn.execute(
                "SELECT * FROM job_suppressions ORDER BY created_at DESC, id LIMIT ?",
                (limit,),
            )
        return [self._row(row) for row in await cur.fetchall()]

    async def list_for_job(self, job_id: str) -> Sequence[SuppressionRecord]:
        cur = await self._conn.execute(
            "SELECT * FROM job_suppressions WHERE job_id = ? ORDER BY created_at, id",
            (job_id,),
        )
        return [self._row(row) for row in await cur.fetchall()]

    async def active_job_ids(self) -> Sequence[str]:
        cur = await self._conn.execute(
            "SELECT DISTINCT job_id FROM job_suppressions WHERE state = 'active'"
        )
        return [str(row[0]) for row in await cur.fetchall()]

    async def restore(self, job_id: str, application_id: str, at: datetime) -> bool:
        cur = await self._conn.execute(
            """
            UPDATE job_suppressions
               SET state = 'restored', updated_at = ?
             WHERE job_id = ? AND application_id = ? AND state != 'restored'
            """,
            (_iso(at), job_id, application_id),
        )
        return cur.rowcount > 0

    async def expire(self, now: datetime) -> Sequence[SuppressionRecord]:
        """Move every lapsed ``active`` row to ``expired``; return them."""
        stamp = _iso(now)
        cur = await self._conn.execute(
            "SELECT * FROM job_suppressions WHERE state = 'active' "
            "AND expires_at IS NOT NULL AND expires_at <= ?",
            (stamp,),
        )
        lapsed = [self._row(row) for row in await cur.fetchall()]
        if lapsed:
            await self._conn.execute(
                "UPDATE job_suppressions SET state = 'expired', updated_at = ? "
                "WHERE state = 'active' AND expires_at IS NOT NULL AND expires_at <= ?",
                (stamp, stamp),
            )
        return lapsed

    async def clear_restored(self, job_id: str, application_id: str) -> bool:
        """Drop a restore marker so automation may evaluate the pair again."""
        cur = await self._conn.execute(
            "DELETE FROM job_suppressions WHERE job_id = ? AND application_id = ? "
            "AND state = 'restored'",
            (job_id, application_id),
        )
        return cur.rowcount > 0

    async def delete_for_application(self, application_id: str) -> int:
        cur = await self._conn.execute(
            "DELETE FROM job_suppressions WHERE application_id = ?", (application_id,)
        )
        return cur.rowcount

    async def clear_job(self, job_id: str) -> int:
        cur = await self._conn.execute(
            "DELETE FROM job_suppressions WHERE job_id = ? AND state != 'restored'",
            (job_id,),
        )
        return cur.rowcount

    async def get_policy(self) -> Mapping[str, object] | None:
        cur = await self._conn.execute(
            "SELECT policy_json FROM suppression_policies WHERE profile_id IS NULL"
        )
        row = await cur.fetchone()
        return _json_map(row[0]) if row is not None else None

    async def put_policy(self, policy: Mapping[str, object], at: datetime) -> None:
        stamp = _iso(at)
        payload = json.dumps(dict(policy), sort_keys=True)
        cur = await self._conn.execute(
            "UPDATE suppression_policies SET policy_json = ?, updated_at = ? "
            "WHERE profile_id IS NULL",
            (payload, stamp),
        )
        if cur.rowcount:
            return
        await self._conn.execute(
            "INSERT INTO suppression_policies(id, profile_id, policy_json, created_at, "
            "updated_at) VALUES (?, NULL, ?, ?, ?)",
            (new_id(), payload, stamp, stamp),
        )


class SqliteJobUserStateRepository:
    def __init__(self, conn: aiosqlite.Connection, clock_now: Any) -> None:
        self._conn = conn
        self._now = clock_now
        self._conn.row_factory = aiosqlite.Row

    async def set(
        self, job_id: str, state: str, *, note: str | None, updated_at: datetime
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO job_user_state(job_id, state, note, updated_at)
            VALUES (?,?,?,?)
            ON CONFLICT(job_id) DO UPDATE SET
              state = excluded.state,
              note = excluded.note,
              updated_at = excluded.updated_at
            """,
            (job_id, state, note, _iso(updated_at)),
        )

    async def get(self, job_id: str) -> Mapping[str, object] | None:
        cur = await self._conn.execute(
            "SELECT * FROM job_user_state WHERE job_id = ?", (job_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row is not None else None

    async def get_many(self, job_ids: Sequence[str]) -> Mapping[str, str]:
        wanted = [job_id for job_id in dict.fromkeys(job_ids) if job_id]
        out: dict[str, str] = {}
        for start in range(0, len(wanted), 400):
            chunk = wanted[start : start + 400]
            placeholders = ",".join("?" for _ in chunk)
            cur = await self._conn.execute(
                f"SELECT job_id, state FROM job_user_state WHERE job_id IN ({placeholders})",
                tuple(chunk),
            )
            for row in await cur.fetchall():
                out[str(row["job_id"])] = str(row["state"])
        return out

    async def list(
        self, *, state: str | None = None, limit: int = 200
    ) -> Sequence[Mapping[str, object]]:
        if state:
            cur = await self._conn.execute(
                "SELECT * FROM job_user_state WHERE state = ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (state, limit),
            )
        else:
            cur = await self._conn.execute(
                "SELECT * FROM job_user_state ORDER BY updated_at DESC LIMIT ?", (limit,)
            )
        return [dict(row) for row in await cur.fetchall()]

    async def hidden_job_ids(self) -> Sequence[str]:
        cur = await self._conn.execute(
            "SELECT job_id FROM job_user_state WHERE state IN ('dismissed','hidden')"
        )
        return [str(row[0]) for row in await cur.fetchall()]

    async def clear(self, job_id: str) -> bool:
        cur = await self._conn.execute(
            "DELETE FROM job_user_state WHERE job_id = ?", (job_id,)
        )
        return cur.rowcount > 0


def new_id() -> str:
    return str(uuid.uuid4())
