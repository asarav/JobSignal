"""Application tracking and suppression use cases (spec §8, M7).

Application rows are the user's own history: creating one records the job as
it looks *now* (title, description, locations), so a later suppression is
explained against what was applied for rather than today's posting. Nothing in
this module can submit anything; it only records what the user tells it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from app.application import suppression as suppression_glue
from app.application.config import live_config
from app.application.container import Container
from app.application.dto import (
    ApplicationCreateParams,
    ApplicationEventParams,
    ApplicationInfo,
    ApplicationListResult,
    ApplicationOutcomeParams,
    ApplicationStatusParams,
    CompanyHistoryResult,
    JobUserStateParams,
    JobUserStateResult,
    OkResult,
    RestoreSuppressedJobParams,
    SuppressionInfo,
    SuppressionListResult,
    SuppressionPolicyParams,
    SuppressionPolicyResult,
)
from app.domain.models.application import (
    APPLICATION_STATUSES,
    OUTCOME_KINDS,
    PRE_APPLICATION_STATUSES,
    Application,
    ApplicationEvent,
    ApplicationOutcome,
    SuppressionPolicy,
    SuppressionRecord,
)
from app.domain.models.common import PortError


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise PortError(
            code="validation.invalid_params",
            message=f"invalid timestamp: {value}",
            retryable=False,
        ) from exc


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _require_status(status: str) -> str:
    if status not in APPLICATION_STATUSES:
        raise PortError(
            code="validation.invalid_params",
            message=f"unknown application status: {status}",
            retryable=False,
            details={"allowed": list(APPLICATION_STATUSES)},
        )
    return status


def _applied_at_for(
    status: str, requested: datetime | None, now: datetime
) -> datetime | None:
    """Pre-application states never set ``applied_at`` (spec §8.2 rule 1)."""
    if status in PRE_APPLICATION_STATUSES:
        return None
    return requested or now


def _event(
    application_id: str, kind: str, at: datetime, *, from_status: str | None = None,
    to_status: str | None = None, note: str = "", data: Mapping[str, Any] | None = None,
) -> ApplicationEvent:
    return ApplicationEvent(
        id=uuid.uuid4().hex,
        application_id=application_id,
        kind=kind,
        at=at,
        from_status=from_status,
        to_status=to_status,
        note=note,
        data=dict(data or {}),
    )


class ApplicationsUseCase:
    def __init__(self, container: Container) -> None:
        self._container = container

    async def create(self, params: ApplicationCreateParams) -> ApplicationInfo:
        status = _require_status(params.status)
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            job = await uow.jobs.get(params.job_id)
            if job is None:
                raise PortError(
                    code="not_found",
                    message=f"unknown job: {params.job_id}",
                    retryable=False,
                )
            existing = await uow.applications.find_for_job(params.job_id)
            if existing:
                # Tracking the same job twice is the same record, not a duplicate.
                application = existing[0]
            else:
                applied_at = _applied_at_for(status, _parse_dt(params.applied_at), now)
                raw_locations = job.get("locations")
                locations = raw_locations if isinstance(raw_locations, (list, tuple)) else ()
                application = Application(
                    id=self._container.ids.new_id(),
                    company_id=str(job.get("company_id") or ""),
                    job_id=params.job_id,
                    job_title=str(job.get("title") or ""),
                    job_title_family=job.get("title_family"),  # type: ignore[arg-type]
                    job_canonical_url=str(job.get("canonical_url") or ""),
                    job_description_text=str(job.get("description_text") or ""),
                    job_department=job.get("department"),  # type: ignore[arg-type]
                    job_locations=tuple(
                        entry for entry in locations if isinstance(entry, Mapping)
                    ),
                    job_workplace_type=str(job.get("workplace_type") or "unknown"),
                    job_snapshot_id=None,
                    status=status,
                    applied_at=applied_at,
                    resume_version_id=params.resume_version_id,
                    notes=params.notes,
                    created_at=now,
                    updated_at=now,
                )
                await uow.applications.add(application)
                await uow.applications.add_event(
                    _event(
                        application.id,
                        "created",
                        now,
                        to_status=status,
                        note=params.notes,
                    )
                )
                if applied_at is not None:
                    await uow.applications.add_event(
                        _event(application.id, "applied", now, to_status=status)
                    )
                    await self._after_applied(uow, application, now)
            await uow.commit()
            return await self._detail(uow, application)

    async def update_status(self, params: ApplicationStatusParams) -> ApplicationInfo:
        status = _require_status(params.status)
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            current = await uow.applications.get(params.application_id)
            if current is None:
                raise PortError(
                    code="not_found",
                    message=f"unknown application: {params.application_id}",
                    retryable=False,
                )
            applied_at = current.applied_at
            if status in PRE_APPLICATION_STATUSES:
                # Correcting back to a pre-application state ends the cooldown.
                applied_at = None
            elif applied_at is None:
                applied_at = _parse_dt(params.applied_at) or now
            elif params.applied_at:
                applied_at = _parse_dt(params.applied_at)

            updated = await uow.applications.save(
                current.id,
                status=status,
                applied_at=applied_at,
                notes=current.notes,
                resume_version_id=current.resume_version_id,
                updated_at=now,
            )
            await uow.applications.add_event(
                _event(
                    current.id,
                    "status_changed",
                    now,
                    from_status=current.status,
                    to_status=status,
                    note=params.note,
                )
            )
            if applied_at is None:
                await uow.suppressions.delete_for_application(current.id)
            elif current.applied_at is None:
                await self._after_applied(uow, updated or current, now)
            await uow.commit()
            return await self._detail(uow, updated or current)

    async def add_event(self, params: ApplicationEventParams) -> OkResult:
        kind = params.kind.strip()
        if not kind:
            raise PortError(
                code="validation.invalid_params",
                message="event kind must not be empty",
                retryable=False,
            )
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            if await uow.applications.get(params.application_id) is None:
                raise PortError(
                    code="not_found",
                    message=f"unknown application: {params.application_id}",
                    retryable=False,
                )
            await uow.applications.add_event(
                _event(
                    params.application_id,
                    kind,
                    now,
                    note=params.note,
                    data=params.data,
                )
            )
            await uow.commit()
        return OkResult()

    async def record_outcome(self, params: ApplicationOutcomeParams) -> OkResult:
        if params.kind not in OUTCOME_KINDS:
            raise PortError(
                code="validation.invalid_params",
                message=f"unknown outcome kind: {params.kind}",
                retryable=False,
                details={"allowed": list(OUTCOME_KINDS)},
            )
        now = self._container.clock.now()
        occurred_at = _parse_dt(params.occurred_at) or now
        async with self._container.uow_factory() as uow:
            if await uow.applications.get(params.application_id) is None:
                raise PortError(
                    code="not_found",
                    message=f"unknown application: {params.application_id}",
                    retryable=False,
                )
            await uow.applications.add_outcome(
                ApplicationOutcome(
                    id=self._container.ids.new_id(),
                    application_id=params.application_id,
                    kind=params.kind,
                    occurred_at=occurred_at,
                    note=params.note,
                    proposed=False,
                    confirmed=True,
                    created_at=now,
                )
            )
            await uow.applications.add_event(
                _event(
                    params.application_id,
                    "outcome_recorded",
                    now,
                    note=params.note,
                    data={"kind": params.kind, "occurred_at": occurred_at.isoformat()},
                )
            )
            await uow.commit()
        return OkResult()

    async def list(self) -> ApplicationListResult:
        async with self._container.uow_factory() as uow:
            rows = await uow.applications.list(limit=200)
            applications = [await self._detail(uow, row) for row in rows]
            await uow.commit()
        return ApplicationListResult(
            applications=applications, total=len(applications)
        )

    async def get_company_history(self, company_id: str) -> CompanyHistoryResult:
        async with self._container.uow_factory() as uow:
            rows = [
                row
                for row in await uow.applications.list(limit=1000)
                if row.company_id == company_id
            ]
            applications = [await self._detail(uow, row) for row in rows]
            await uow.commit()
        applied = [row.applied_at for row in applications if row.applied_at]
        return CompanyHistoryResult(
            company_id=company_id,
            applications=applications,
            total=len(applications),
            total_applied=len(applied),
            last_applied_at=max(applied) if applied else None,
        )

    async def _detail(self, uow: Any, application: Application) -> ApplicationInfo:
        events = await uow.applications.list_events(application.id)
        outcomes = await uow.applications.list_outcomes(application.id)
        suppressions = await uow.suppressions.list_for_job(application.job_id)
        return ApplicationInfo(
            application_id=application.id,
            company_id=application.company_id,
            job_id=application.job_id,
            job_title=application.job_title,
            job_title_family=application.job_title_family,
            status=application.status,
            applied_at=_iso(application.applied_at),
            resume_version_id=application.resume_version_id,
            notes=application.notes,
            created_at=_iso(application.created_at),
            updated_at=_iso(application.updated_at),
            events=[
                {
                    "kind": event.kind,
                    "at": _iso(event.at),
                    "from_status": event.from_status,
                    "to_status": event.to_status,
                    "note": event.note,
                    "data": dict(event.data),
                }
                for event in events
            ],
            outcomes=[
                {
                    "kind": outcome.kind,
                    "occurred_at": _iso(outcome.occurred_at),
                    "note": outcome.note,
                    "proposed": outcome.proposed,
                    "confirmed": outcome.confirmed,
                }
                for outcome in outcomes
            ],
            suppressions=[
                {
                    "job_id": record.job_id,
                    "state": record.state,
                    "reason": record.reason,
                    "expires_at": _iso(record.expires_at),
                }
                for record in suppressions
            ],
        )

    async def _after_applied(self, uow: Any, application: Application, now: datetime) -> None:
        """A job reached ``applied``: re-evaluate the rest of that company."""
        policy = await suppression_glue.load_policy(uow, live_config(self._container))
        await suppression_glue.evaluate_company(
            uow, application.company_id, policy=policy, now=now
        )


class SuppressionsUseCase:
    def __init__(self, container: Container) -> None:
        self._container = container

    async def list(self) -> SuppressionListResult:
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            await self._sweep(uow, now)
            records = await uow.suppressions.list(limit=500)
            rows = [(record, await uow.jobs.get(record.job_id)) for record in records]
            policy = await suppression_glue.load_policy(uow, live_config(self._container))
            await uow.commit()
        suppressions = [_suppression_info(record, job) for record, job in rows]
        return SuppressionListResult(
            suppressions=suppressions,
            total=len(suppressions),
            policy=policy.to_mapping(),
        )

    async def sweep(self) -> int:
        """Lapse due decisions and re-evaluate their jobs.

        The worker calls this on a timer: a cooldown must end when it ends, not
        the next time someone happens to open Previously Seen.
        """
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            expired = await self._sweep(uow, now)
            await uow.commit()
        return expired

    async def restore(self, params: RestoreSuppressedJobParams) -> OkResult:
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            records = await uow.suppressions.list_for_job(params.job_id)
            targets = [
                record
                for record in records
                if params.application_id in (None, record.application_id)
            ]
            if not targets:
                raise PortError(
                    code="not_found",
                    message=f"no suppression for job: {params.job_id}",
                    retryable=False,
                )
            for record in targets:
                await uow.suppressions.restore(record.job_id, record.application_id, now)
            await uow.commit()
        return OkResult()

    async def get_policy(self) -> SuppressionPolicyResult:
        async with self._container.uow_factory() as uow:
            policy = await suppression_glue.load_policy(uow, live_config(self._container))
            await uow.commit()
        return SuppressionPolicyResult(policy=policy.to_mapping())

    async def update_policy(
        self, params: SuppressionPolicyParams
    ) -> SuppressionPolicyResult:
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            current = await suppression_glue.load_policy(uow, live_config(self._container))
            merged = SuppressionPolicy.from_mapping(
                {**current.to_mapping(), **params.policy}
            )
            await uow.suppressions.put_policy(merged.to_mapping(), now)
            await suppression_glue.rebuild(uow, policy=merged, now=now)
            await uow.commit()
        return SuppressionPolicyResult(policy=merged.to_mapping())

    async def set_user_state(self, params: JobUserStateParams) -> JobUserStateResult:
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            if await uow.jobs.get(params.job_id) is None:
                raise PortError(
                    code="not_found",
                    message=f"unknown job: {params.job_id}",
                    retryable=False,
                )
            await uow.job_user_state.set(
                params.job_id, params.state, note=params.note, updated_at=now
            )
            await uow.commit()
        return JobUserStateResult(
            job_id=params.job_id,
            state=params.state,
            hidden=params.state in ("dismissed", "hidden"),
        )

    async def _sweep(self, uow: Any, now: datetime) -> int:
        """Lapse due decisions, then re-evaluate the jobs that expired."""
        expired = await uow.suppressions.expire(now)
        if not expired:
            return 0
        policy = await suppression_glue.load_policy(uow, live_config(self._container))
        applied = list(await uow.applications.list_applied())
        for record in expired:
            row = await uow.jobs.get(record.job_id)
            if row is None:
                continue
            await suppression_glue.evaluate_and_store(
                uow,
                suppression_glue.candidate_from_row(row),
                applied=applied,
                policy=policy,
                now=now,
            )
        return len(expired)


def _suppression_info(
    record: SuppressionRecord, job: Mapping[str, Any] | None
) -> SuppressionInfo:
    return SuppressionInfo(
        suppression_id=record.id,
        job_id=record.job_id,
        application_id=record.application_id,
        job_title=str(job.get("title")) if job is not None else None,
        company_id=str(job.get("company_id")) if job is not None else None,
        state=record.state,
        reason=record.reason,
        explanation=record.explanation,
        similarity=record.similarity,
        expires_at=_iso(record.expires_at),
        created_at=_iso(record.created_at),
        evidence=dict(record.evidence),
        policy=dict(record.policy),
    )

